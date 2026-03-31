import multiprocessing

bind = "0.0.0.0:8000"
worker_class = "sync"
workers = multiprocessing.cpu_count() * 2
accesslog = "-"