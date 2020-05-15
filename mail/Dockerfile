FROM python:3-slim

ADD . /htcondor-mail
WORKDIR /htcondor-mail

RUN pip install -r requirements.txt


ENTRYPOINT ["/htcondor-mail/mail.py"]

