#!/usr/bin/env python3

from premailer import transform
from jinja2 import Environment, FileSystemLoader, select_autoescape
from elasticsearch import Elasticsearch
import elasticsearch_dsl
from elasticsearch_dsl import A, Search, Q, Range
import datetime

import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import argparse

MAXSZ=2**30

import math

def convert_gb(size_bytes):
    return "{:,.2f} GB".format(round(float(size_bytes) / 1024**3, 2))

def convert_size(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return "%s %s" % (s, size_name[i])


def sendMail(html, emails):

    # me == my email address
    # you == recipient's email address
    me = "gracc-support@opensciencegrid.org"
    you = ", ".join(emails)

    # Create message container - the correct MIME type is multipart/alternative.
    msg = MIMEMultipart('alternative')
    msg['Subject'] = "HTCondor Submit Host Transfers"
    msg['From'] = me
    msg['To'] = you

    # Create the body of the message (a plain-text and an HTML version).
    #text = "Hi!\nHow are you?\nHere is the link you wanted:\nhttp://www.python.org"

    # Record the MIME types of both parts - text/plain and text/html.
    #part1 = MIMEText(text, 'plain')
    part2 = MIMEText(html.decode('utf-8'), 'html')

    # Attach parts into message container.
    # According to RFC 2046, the last part of a multipart message, in this case
    # the HTML message, is best and preferred.
    #msg.attach(part1)
    msg.attach(part2)

    # Send the message via local SMTP server.
    s = smtplib.SMTP('localhost')
    # sendmail function takes 3 arguments: sender's address, recipient's address
    # and message to send - here it is sent as one string.
    s.sendmail(me, emails, msg.as_string())
    s.quit()


def getHostBytes(client, starttime, endtime):
    s = Search(using=client, index="htcondor-xfer-stats2-*")
    s = s.filter('range', ** {'@timestamp': {'gte': starttime, 'lt': endtime}})
    # Remove records with more than 1 TB of data transferred, bug:
    # https://htcondor-wiki.cs.wisc.edu/index.cgi/tktview?tn=7575,0
    s = s.filter('range', bytes={'from': 0, 'to': 1024**4})
    bkt = s.aggs
    bkt = bkt.bucket('hosts', 'terms', size=MAXSZ, field='host.name.keyword')
    bkt = bkt.metric('Bytes', 'sum', field='bytes')
    bkt = bkt.metric('loss', 'avg', field='lost')

    print(s.to_dict())

    response = s.execute()
    hosts = {}
    for tag in response.aggregations.hosts:
        hosts[tag.key] = {'bytes':tag.Bytes.value, 'bytes_str':convert_gb(tag.Bytes.value), 'loss': tag.loss.value}

    return hosts

def getLastReported(client, endtime=datetime.datetime.now()):
    s = Search(using=client, index="htcondor-xfer-stats2-*")
    starttime = datetime.datetime.now() - datetime.timedelta(days=365)
    s = s.filter('range', ** {'@timestamp': {'gte': starttime, 'lt': endtime}})
    bkt = s.aggs
    bkt = bkt.bucket('hosts', 'terms', size=MAXSZ, field='host.name.keyword')
    bkt = bkt.bucket('max_time', 'max', field='CreateDate')

    print(s.to_dict())

    response = s.execute()
    hosts = {}
    for tag in response.aggregations.hosts:
        if tag.max_time.value is None:
            continue
        last_seen = datetime.datetime.fromtimestamp(tag.max_time.value/1000)
        # Discount hosts seen in the last week
        if last_seen > datetime.datetime.now() - datetime.timedelta(days=7):
            continue
        hosts[tag.key] = {'max_time':tag.max_time.value, 'max_time_str': last_seen.strftime('%Y-%m-%d %H:%M:%S')}

    return hosts


def setArgs():
    parser = argparse.ArgumentParser()
    parser.add_argument('emails', metavar='emails', type=str, nargs='+',
                        help='Emails to send the report')

    args = parser.parse_args()
    return args

def main():

    args = setArgs()

    # Get some stats from ES
    client = Elasticsearch(
        ["https://gracc.opensciencegrid.org/q"],
        timeout=300, use_ssl=True, verify_certs=True)
    
    endtime = datetime.datetime.now()
    starttime = datetime.datetime.now() - datetime.timedelta(days=7)

    hosts = getHostBytes(client, starttime, endtime)
    total_transferred = 0
    for host in hosts.keys():
        total_transferred += hosts[host]['bytes']

    # Get hosts from last week
    endtime = starttime
    starttime = datetime.datetime.now() - datetime.timedelta(days=14)
    last_week_hosts = getHostBytes(client, starttime, endtime)
    last_week_hosts_set = set(last_week_hosts.keys())
    hosts_set = set(hosts.keys())
    missing_hosts = last_week_hosts_set - hosts_set
    for host in hosts:
        if host in last_week_hosts:
            hosts[host]['last_week_bytes'] = last_week_hosts[host]['bytes']
            hosts[host]['last_week_bytes_str'] = convert_gb(hosts[host]['last_week_bytes'])
            hosts[host]['delta'] = (float(hosts[host]['bytes'] - last_week_hosts[host]['bytes']) / max(last_week_hosts[host]['bytes'], 1)) * 100
            # Round the output
            hosts[host]['delta'] = round(hosts[host]['delta'], 1)
        else:
            hosts[host]['delta'] = 100
            hosts[host]['last_week_bytes'] = 0
            hosts[host]['last_week_bytes_str'] = "N/A"

    total_transferred = convert_size(total_transferred)

    last_reported = getLastReported(client)

    env = Environment(
        loader=FileSystemLoader('templates'),
        autoescape=select_autoescape(['html', 'xml'])
    )

    template = env.get_template('mail.html')
    output_html = transform(template.render(total_transferred=total_transferred, hosts=hosts, missing_hosts = list(missing_hosts), last_reported=last_reported))
    #with open('output.html', 'w') as output:
    #    output.write(output_html)

    sendMail(output_html.encode('utf-8'), args.emails)




if __name__ == "__main__":
    main()

