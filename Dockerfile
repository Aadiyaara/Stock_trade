FROM public.ecr.aws/lambda/python:3.12

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY lambda/ lambda/

CMD ["lambda/handler.morning_buy"]
