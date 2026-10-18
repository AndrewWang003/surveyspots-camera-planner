#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="${SURVEYSPOTS_DB_CONTAINER:-postgis}"
IMAGE_NAME="${SURVEYSPOTS_DB_IMAGE:-postgis/postgis}"
VOLUME_NAME="${SURVEYSPOTS_DB_VOLUME:-surveyspots_postgis_data}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-surveypass}"

container_exists() {
  docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1
}

container_running() {
  [ "$(docker container inspect -f '{{.State.Running}}' "${CONTAINER_NAME}" 2>/dev/null || true)" = "true" ]
}

start_database() {
  if container_running; then
    echo "SurveySpots database is already running."
  elif container_exists; then
    docker start "${CONTAINER_NAME}" >/dev/null
    echo "SurveySpots database started."
  else
    docker run \
      --name "${CONTAINER_NAME}" \
      --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
      --publish 5432:5432 \
      --volume "${VOLUME_NAME}:/var/lib/postgresql/data" \
      --detach \
      "${IMAGE_NAME}" >/dev/null
    echo "SurveySpots database container created and started."
  fi

  for _ in {1..30}; do
    if docker exec "${CONTAINER_NAME}" pg_isready -U postgres -d postgres >/dev/null 2>&1; then
      echo "PostGIS is accepting connections on localhost:5432."
      return
    fi
    sleep 1
  done

  echo "Database started but did not become ready within 30 seconds." >&2
  exit 1
}

stop_database() {
  if container_running; then
    docker stop "${CONTAINER_NAME}" >/dev/null
    echo "SurveySpots database stopped."
  elif container_exists; then
    echo "SurveySpots database is already stopped."
  else
    echo "SurveySpots database container does not exist."
  fi
}

show_status() {
  if ! container_exists; then
    echo "SurveySpots database container does not exist."
    exit 1
  fi

  docker container inspect --format 'Container: {{.Name}}' "${CONTAINER_NAME}"
  docker container inspect --format 'State: {{.State.Status}}' "${CONTAINER_NAME}"
  docker container inspect --format 'Image: {{.Config.Image}}' "${CONTAINER_NAME}"
  if container_running; then
    docker exec "${CONTAINER_NAME}" pg_isready -U postgres -d postgres
  fi
}

case "${1:-}" in
  start)
    start_database
    ;;
  stop)
    stop_database
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
