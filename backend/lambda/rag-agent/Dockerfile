FROM public.ecr.aws/lambda/python:3.11

# Copy requirements and install
COPY requirements-full.txt ${LAMBDA_TASK_ROOT}/
RUN pip install -r ${LAMBDA_TASK_ROOT}/requirements-full.txt --target "${LAMBDA_TASK_ROOT}"

# Copy function code
COPY *.py ${LAMBDA_TASK_ROOT}/

# Set the CMD to handler
CMD [ "handler.lambda_handler" ]
