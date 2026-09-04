"""Fixed one-shot worker entrypoint для Cloudflare Workers AI."""

from .cloudflare_workers_ai import worker_main

if __name__ == "__main__":
    worker_main()
