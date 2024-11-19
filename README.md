# Grafana on Docker Swarm

## Development setup

From the root of this project, run these commands:

1. Create the `***.env` files

   ```
   ./scripts/init-docker-compose-stack-env.sh ./docker-compose.minio.yml
   ./scripts/init-docker-compose-stack-env.sh ./docker-compose.grafana-stack.yml
   ```

2. Modify any additional config options in the `***.env` files.

3. Run the dev compose stack

   ```
   sudo docker compose \
       -f ./docker-swarm-templates/docker-compose.minio.yml \
       --env-file ./docker-swarm-templates/minio.env \
       up -d

   sudo docker compose \
       -f ./docker-swarm-templates/docker-compose.grafana-stack.yml \
       --env-file ./docker-swarm-templates/grafana-stack.env \
       up -d
   ```
