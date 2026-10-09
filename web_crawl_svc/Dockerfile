FROM public.ecr.aws/lambda/python:3.12
COPY web_crawl_svc ${LAMBDA_TASK_ROOT}/web_crawl_svc
RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}/web_crawl_svc
CMD ["web_crawl_svc.handler.lambda_handler"]
