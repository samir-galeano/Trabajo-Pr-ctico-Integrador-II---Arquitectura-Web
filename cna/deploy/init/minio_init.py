"""Crea los buckets de MinIO que usan export-svc y media-svc.

Se ejecuta como contenedor de una sola vez (servicio minio-init del compose).
Es idempotente: si un bucket ya existe, no lo toca.
"""
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
BUCKETS = [b.strip() for b in os.getenv("MINIO_BUCKETS", "").split(",") if b.strip()]

# Usa las credenciales raíz de MinIO (cna/deploy/minio/.env)
s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=os.getenv("MINIO_ACCESS_KEY") or os.environ["MINIO_ROOT_USER"],
    aws_secret_access_key=os.getenv("MINIO_SECRET_KEY") or os.environ["MINIO_ROOT_PASSWORD"],
)


def wait_for_minio(attempts=30, delay=2):
    for attempt in range(1, attempts + 1):
        try:
            s3.list_buckets()
            return
        except Exception as exc:
            print(f"[minio-init] MinIO no disponible ({attempt}/{attempts}): {exc}", flush=True)
            time.sleep(delay)
    print("[minio-init] No se pudo conectar a MinIO", flush=True)
    sys.exit(1)


def main():
    if not BUCKETS:
        print("[minio-init] MINIO_BUCKETS vacío, nada que crear", flush=True)
        return

    wait_for_minio()

    for bucket in BUCKETS:
        try:
            s3.head_bucket(Bucket=bucket)
            print(f"[minio-init] Bucket {bucket} ya existe", flush=True)
        except ClientError:
            s3.create_bucket(Bucket=bucket)
            print(f"[minio-init] Bucket {bucket} creado", flush=True)


if __name__ == "__main__":
    main()
