# -*- coding: utf-8 -*-
"""Start the RQ worker used by Docker Compose."""

from redis import Redis
from rq import Queue, Worker

import config
from storage.product_store import get_product_store


def main():
    get_product_store()
    connection = Redis.from_url(config.REDIS_URL)
    queue = Queue(config.RQ_QUEUE_NAME, connection=connection)
    worker = Worker([queue], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == '__main__':
    main()
