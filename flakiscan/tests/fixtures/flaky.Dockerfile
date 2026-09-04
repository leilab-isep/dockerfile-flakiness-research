FROM ubuntu:latest

ARG BUILD_ENV

RUN apt-get update && apt-get install -y curl
RUN curl http://example.com/install.sh | bash
RUN git clone https://github.com/example/repo.git
RUN wget https://example.com/archive.tar.gz -O archive.tar.gz
ADD https://example.com/data.tar.gz /data.tar.gz

RUN pip install requests

COPY . /app
WORKDIR /app
CMD ["python3", "app.py"]
