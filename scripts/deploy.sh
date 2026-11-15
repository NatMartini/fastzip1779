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
previous_tag=''
if [[ -f /opt/fastzip/current-tag ]]; then previous_tag=$(cat /opt/fastzip/current-tag); fi
migration_name="fastzip-migrate-${requested_tag:0:12}"
cleanup() { docker service rm "$migration_name" >/dev/null 2>&1 || true; }
rollback() {
  code=$?
  trap - ERR
  cleanup
  if [[ -n "$previous_tag" ]]; then
    export IMAGE_TAG="$previous_tag"
    docker stack deploy --with-registry-auth -c deploy/stack.yaml fastzip
    python3 scripts/wait_stack.py --tag "$previous_tag" || true
    echo "Redeployed previous image tag $previous_tag"
  fi
  exit "$code"
}
trap rollback ERR
trap cleanup EXIT
docker stack config -c deploy/stack.yaml >/dev/null
docker stack deploy --with-registry-auth -c deploy/bootstrap.yaml fastzip
for _ in $(seq 1 60); do
  database_container=$(docker ps --filter label=com.docker.swarm.service.name=fastzip_postgres --format '{{.ID}}')
  if [[ -n "$database_container" ]] && docker exec "$database_container" pg_isready -U fastzip -d fastzip >/dev/null; then break; fi
  sleep 2
done
docker service create --detach=true --name "$migration_name" --restart-condition none \
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
  state=$(docker service ps "$migration_name" --format '{{.CurrentState}}' | head -n 1)
  if [[ "$state" == Complete* ]]; then migrated=true; break; fi
  if [[ "$state" == Failed* || "$state" == Rejected* ]]; then echo 'Database migration failed'; false; fi
  sleep 2
done
[[ "$migrated" == true ]]
cleanup
docker stack deploy --with-registry-auth -c deploy/stack.yaml fastzip
python3 scripts/wait_stack.py --tag "$IMAGE_TAG"
docker run --rm --network host "$REGISTRY/api:$IMAGE_TAG" python scripts/smoke.py --base-url "https://$DOMAIN"
printf '%s\n' "$IMAGE_TAG" > /opt/fastzip/current-tag
echo "Deployed and smoke-tested $IMAGE_TAG"
