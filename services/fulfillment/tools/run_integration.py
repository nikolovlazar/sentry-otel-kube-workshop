#!/usr/bin/env python3
"""Run integration tests against disposable, uniquely named local containers."""
import os
import secrets
import subprocess
import time

suffix = secrets.token_hex(5)
postgres_name = f"workshop-fulfillment-test-postgres-{suffix}"
redis_name = f"workshop-fulfillment-test-redis-{suffix}"
created = []

def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()

try:
    docker("run", "-d", "--rm", "--name", postgres_name,
           "--label", "workshop.test=fulfillment", "-e", "POSTGRES_PASSWORD=workshop_test_password",
           "-e", "POSTGRES_DB=fulfillment_test", "-p", "127.0.0.1::5432", "postgres:17.10")
    created.append(postgres_name)
    docker("run", "-d", "--rm", "--name", redis_name,
           "--label", "workshop.test=fulfillment", "-p", "127.0.0.1::6379", "redis:7.4")
    created.append(redis_name)
    for _ in range(60):
        ready = subprocess.run(["docker", "exec", postgres_name, "pg_isready", "-U", "postgres"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ready.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("test PostgreSQL was not ready within 30 seconds")
    postgres_port = docker("port", postgres_name, "5432/tcp").rsplit(":", 1)[1]
    redis_port = docker("port", redis_name, "6379/tcp").rsplit(":", 1)[1]
    test_env = dict(os.environ)
    test_env["TEST_DATABASE_URL"] = f"postgres://postgres:workshop_test_password@127.0.0.1:{postgres_port}/fulfillment_test?sslmode=disable"
    test_env["TEST_REDIS_URL"] = f"redis://127.0.0.1:{redis_port}"
    subprocess.run(["go", "test", "-race", "./...", "-count=1", "-v"], env=test_env, check=True)
finally:
    for name in reversed(created):
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
