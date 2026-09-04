# flakiscan-ignore: DL3007
FROM ubuntu:latest

# flakiscan-ignore
RUN apt-get install -y curl

RUN pip install requests
