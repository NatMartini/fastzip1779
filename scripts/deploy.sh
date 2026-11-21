#!/usr/bin/env bash
set -euo pipefail
requested_tag=${1:?Usage: scripts/deploy.sh COMMIT_SHA}
[[ "$requested_tag" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full commit SHA'; exit 1; }
exec 9>/opt/fastzip/deploy.lock
flock -n 9 || { echo 'Another deployment is active'; exit 1; }
set -a
source /opt/fastzip/production.env
set +a
export IMAGE_TAG="$requested_tag"
# Stop-first worker updates drain one worker at a time, up to its 330-second grace period.
worker_replicas=${WORKER_REPLICAS:-2}
if [[ ! "$worker_replicas" =~ ^[0-9]+$ ]]; then
  echo 'WORKER_REPLICAS must be a nonnegative integer' >&2
  exit 1
fi
convergence_timeout=$((240 + 380 * 10#$worker_replicas))
previous_tag=''
if [[ -f /opt/fastzip/current-tag ]]; then previous_tag=$(cat /opt/fastzip/current-tag); fi
if [[ -n "$previous_tag" && ! "$previous_tag" =~ ^[a-f0-9]{40}$ ]]; then
  echo 'Invalid previous release tag in /opt/fastzip/current-tag' >&2
  exit 1
fi
if [[ ! -d "$POSTGRES_VOLUME_PATH" ]] ||
  ! volume_mount=$(findmnt -nr -o TARGET --target "$POSTGRES_VOLUME_PATH") ||
  [[ -z "$volume_mount" || "$volume_mount" == / ]]; then
  echo 'POSTGRES_VOLUME_PATH must be a directory on a mounted database Volume' >&2
  exit 1
fi
migration_name="fastzip-migrate-${requested_tag:0:12}"
cleanup() { docker service rm "$migration_name" >/dev/null 2>&1 || true; }
rollback() {
  local code=$? previous_stack
  trap - ERR
  if [[ -n "$previous_tag" ]]; then
    export IMAGE_TAG="$previous_tag"
    previous_stack="/opt/fastzip/releases/$previous_tag/deploy/stack.yaml"
    if [[ ! -f "$previous_stack" ]]; then previous_stack=deploy/stack.yaml; fi
    if docker stack deploy --resolve-image changed --with-registry-auth -c "$previous_stack" fastzip &&
      python3 scripts/wait_stack.py --tag "$previous_tag" --timeout "$convergence_timeout" --allow-rolled-back; then
      echo "Swarm converged on previous image tag $previous_tag" >&2
    else
      echo "Could not confirm rollback to previous image tag $previous_tag" >&2
    fi
  else
    echo 'Initial rollout failed. There is no previously successful release tag.' >&2
  fi
  exit "$code"
}
docker stack config -c deploy/stack.yaml >/dev/null
if ! docker service inspect fastzip_postgres >/dev/null 2>&1; then
  docker stack deploy --with-registry-auth -c deploy/bootstrap.yaml fastzip
fi
database_ready=false
for _ in $(seq 1 60); do
  database_container=$(docker ps --filter label=com.docker.swarm.service.name=fastzip_postgres --format '{{.ID}}')
  if [[ -n "$database_container" ]] &&
    docker exec "$database_container" pg_isready -h 127.0.0.1 -U fastzip -d fastzip >/dev/null; then
    database_ready=true
    break
  fi
  sleep 2
done
if [[ "$database_ready" != true ]]; then
  echo 'Database did not become ready. Migration and application rollout were skipped.' >&2
  exit 1
fi
trap cleanup EXIT
docker service create --detach=true --no-healthcheck --name "$migration_name" --restart-condition none \
  --constraint node.role==manager --network fastzip_private --with-registry-auth \
  --secret source=fastzip_db_password,target=db_password \
  --secret source=fastzip_session_secret,target=session_secret \
  --secret source=fastzip_spaces_access,target=spaces_access \
  --secret source=fastzip_spaces_secret,target=spaces_secret \
  --env ENVIRONMENT=production --env DB_HOST=postgres --env DB_PASSWORD_FILE=/run/secrets/db_password \
  --env SESSION_SECRET_FILE=/run/secrets/session_secret --env SECURE_COOKIES=true \
  --env "PUBLIC_ORIGIN=https://$DOMAIN" --env "S3_ENDPOINT=$SPACES_ENDPOINT" \
  --env S3_ACCESS_KEY_FILE=/run/secrets/spaces_access --env S3_SECRET_KEY_FILE=/run/secrets/spaces_secret \
  "$REGISTRY/api:$IMAGE_TAG" fastzip migrate >/dev/null
migrated=false
for _ in $(seq 1 90); do
  state=$(docker service ps "$migration_name" --format '{{.CurrentState}}')
  state=${state%%$'\n'*}
  if [[ "$state" == Complete* ]]; then migrated=true; break; fi
  if [[ "$state" == Failed* || "$state" == Rejected* ]]; then break; fi
  sleep 2
done
if [[ "$migrated" != true ]]; then
  echo 'Database migration failed or did not finish before the deadline' >&2
  docker service ps --no-trunc "$migration_name" >&2 || true
  exit 1
fi
cleanup
trap - EXIT
trap rollback ERR
docker stack deploy --resolve-image changed --with-registry-auth -c deploy/stack.yaml fastzip
# Docker Swarm stack deploy does not apply the Compose tmpfs setting.
# Explicitly mount writable /tmp while keeping the root filesystem read-only.
for service in backup worker cleanup api; do
  case "$service" in
    api|worker) tmpfs_size=268435456 ;;
    backup) tmpfs_size=134217728 ;;
    *) tmpfs_size=67108864 ;;
  esac

  service_name="fastzip_$service"

  # Avoid adding a duplicate mount on subsequent deployments.
  if ! docker service inspect "$service_name" \
    --format '{{json .Spec.TaskTemplate.ContainerSpec.Mounts}}' |
    grep -q '"Target":"/tmp"'; then
    docker service update \
      --detach=true \
      --mount-add "type=tmpfs,destination=/tmp,tmpfs-size=$tmpfs_size" \
      "$service_name"
  fi
done
python3 scripts/wait_stack.py --tag "$IMAGE_TAG" --timeout "$convergence_timeout"
docker run --rm --network host "$REGISTRY/api:$IMAGE_TAG" python scripts/smoke.py --base-url "https://$DOMAIN"
printf '%s\n' "$IMAGE_TAG" > /opt/fastzip/current-tag.next
mv /opt/fastzip/current-tag.next /opt/fastzip/current-tag
trap - ERR
echo "Deployed and smoke-tested $IMAGE_TAG"
