#! /usr/bin/env python

"""
Script to analyse the download speeds from the four Playstation Network
CDN providers (Akamai, CloudFront, Limelight and Level3) and log the results
to a sqlite DB.
"""

import subprocess
import re
import time
import datetime
import argparse
import sys
import sqlite3
import csv
import json
from random import randint

__author__ = "MacroPolo"
__copyright__ = "Copyright 2017, MacroPolo"
__license__ = "MIT"
__version__ = "1.0.0"
__maintainer__ = "MacroPolo"
__email__ = "contact@slashtwentyfour.net"
__status__ = "Production"


def load_json(config_location):
    """Verify that the config file can be parsed by JSON and then load the parameters."""
    with open(config_location, 'r') as f:
        try:
            config = json.load(f)
            json_domain = config['options']['download_domain']
            json_path = config['options']['download_path']
            json_size = config['options']['download_size']
            json_delete = config['options']['delete_period']
        except:
            print 'ERROR: Failed to load JSON configuration. Please verify your', \
            'config file with a JSON validator e.g. http://jsonlint.com/'
            sys.exit(1)
    return json_domain, json_path, json_size, json_delete


def dns(json_domain):
    """ Perform a DNS lookup for the PSN download domain and record the output in
    a SQL table.
    """
    conn = sqlite3.connect('psn.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS dns
    (TIMESTAMP      DATETIME    PRIMARY KEY     NOT NULL,
    CDN             TEXT        NOT NULL,
    IP              TEXT        NOT NULL
    )''')

    timestamp = time.time()
    timestamp = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    psn_domain = json_domain
    dig_cmd = 'dig +short %s' % psn_domain

    with open("stdout_dig.log", 'w') as stdout_file:
        proc = subprocess.Popen(dig_cmd, shell=True, stdout=stdout_file)
    proc.communicate()

    with open("stdout_dig.log", 'r') as stdout_file:
        for i, line in enumerate(stdout_file):
            # find the CDN in the DNS response
            if i == 2:
                cdn = line.rstrip('\n')
                if "edgesuite.net" in cdn:
                    cdn = 'Akamai'
                elif "l02.cdn" in cdn:
                    cdn = 'Limelight'
                elif "footprint.net" in cdn:
                    cdn = 'Level3'
                elif "cloudfront.net" in cdn:
                    cdn = 'CloudFront'
            # find the first IP address returned in the DNS response then exit loop
            ip_matches = re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", line)
            if ip_matches:
                ip = ip_matches[0]
                break

    c.execute("INSERT INTO dns (TIMESTAMP,CDN,IP) \
                 VALUES (?, ?, ?)", (timestamp, cdn, ip))
    conn.commit()
    conn.close()
    return()


def download(json_domain, json_path, json_size):
    """ Download [n] Bytes from a random IP addresses from each CDN provider and record
    the throughput information in a SQL table.
    """
    conn = sqlite3.connect('psn.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS download
    (TIMESTAMP      DATETIME        NOT NULL,
    CDN             TEXT            NOT NULL,
    IP              TEXT            NOT NULL,
    BANDWIDTH       DECIMAL(5,2)    NOT NULL
    )''')

    timestamp = time.time()
    timestamp = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    # retrieve a random IP addresses from each CDN
    c.execute('SELECT ip FROM dns WHERE cdn = "Akamai" ORDER BY RANDOM() LIMIT 1')
    akamai_ip = c.fetchone()
    c.execute('SELECT ip FROM dns WHERE cdn = "Limelight" ORDER BY RANDOM() LIMIT 1')
    limelight_ip = c.fetchone()
    c.execute('SELECT ip FROM dns WHERE cdn = "Level3" ORDER BY RANDOM() LIMIT 1')
    lvl3_ip = c.fetchone()
    c.execute('SELECT ip FROM dns WHERE cdn = "CloudFront" ORDER BY RANDOM() LIMIT 1')
    cloudfront_ip = c.fetchone()

    cdn_ips = [akamai_ip, limelight_ip, lvl3_ip, cloudfront_ip]
    cdn_names = ['Akamai', 'Limelight', 'Level3', 'CloudFront']

    # retrieve top 3 performing IP addresses
    c.execute('SELECT ip FROM output ORDER BY avg_bw DESC LIMIT 3')
    top_ips = c.fetchall()
    # retrieve the 3 assocaited CDNs
    c.execute('SELECT cdn FROM output ORDER BY avg_bw DESC LIMIT 3')
    top_cdns = c.fetchall()

    for top_ip in top_ips:
        cdn_ips.append(top_ip)
    for top_cdn in top_cdns:
        cdn_names.append(top_cdn[0])

    for ip_addr, name in zip(cdn_ips, cdn_names):
        if ip_addr is None:
            print "No IP address has been resolved for %s yet, checking next CDN provider." % name
            continue
	    # PSN download URL
        download_url = "http://%s%s" % (ip_addr[0], json_path)
	    # -o write file to /dev/null
	    # -s silent mode
	    # -L follow redirects
        curl_output = '-o /dev/null -s -L'
	    # -w Write out useful information to STDOUT
        curl_write = '-w "Average Download:%{speed_download}\nTotal Time:%{time_total}\n\n"'
	    # How many bytes to request in GET
        curl_range = '--header "Range: bytes=0-%s"' % json_size
	    # Host header
        curl_host = '-H "Host: %s"' % json_domain

        curl_cmd = 'curl %s %s %s %s %s' \
                    % (curl_output, curl_host, curl_range, curl_write, download_url)

        with open("stdout_curl.log", 'w') as stdout_file:
            proc = subprocess.Popen(curl_cmd, shell=True, stdout=stdout_file)
        proc.communicate()

	    # Retrieve the throughput information from the STDOUT file and
	    # convert from Bytes/s to Mbit/s
        with open("stdout_curl.log", 'r') as stdout_file:
            for line in stdout_file:
                throughput = re.findall(r"(?<=Download\:)\d.+(?=\.)", line)
                throughput = int(throughput[0]) * 8.0 / 1000000
                throughput = "%.2f" % throughput
                break
        print timestamp, type(timestamp), name, type(name), ip_addr[0], type(ip_addr[0]), throughput, type(throughput)
        c.execute("INSERT INTO download (TIMESTAMP,CDN,IP,BANDWIDTH) \
                 VALUES (?, ?, ?, ?)", (timestamp, name, ip_addr[0], throughput))
        conn.commit()

	    # sleep for a few seconds before downloading from the next CDN
        time.sleep(5)

    conn.close()
    return()


def create_csv():
    """Read data from the sqlite DB and generate a CSV for displaying on a webpage."""
    conn = sqlite3.connect('psn.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS output
    (IP             TEXT            NOT NULL,
    CDN             TEXT            NOT NULL,
    MIN_BW          DECIMAL(18,2)   NOT NULL,
    MAX_BW          DECIMAL(18,2)   NOT NULL,
    AVG_BW          DECIMAL(18,2)   NOT NULL,
    TESTS           INTEGER         NOT NULL,
    RESOLUTIONS     INTEGER         NOT NULL,
    PERCENTAGE      DECIMAL(18,2)   NOT NULL,
    LAST_RESOLVED   TEXT            NOT NULL
    )''')

    # Get list of unique IP addresses that we have resolved
    c.execute('SELECT DISTINCT ip FROM dns')
    ip_list = c.fetchall()

    # Delete the old values from the 'output' table
    c.execute('DELETE FROM output')

    for ip in ip_list:
        # Get CDN associated with IP
        c.execute('SELECT cdn FROM dns WHERE ip = ? LIMIT 1', ip)
        cdn = c.fetchone()
	    # Get minimum bandwidth from IP
        c.execute('SELECT MIN(bandwidth) FROM download WHERE ip = ? LIMIT 1', ip)
        min_bw = c.fetchone()
        if min_bw[0] is None:
            min_bw = ['0']
        # Get maximum bandwidth from IP
        c.execute('SELECT MAX(bandwidth) FROM download WHERE ip = ? LIMIT 1', ip)
        max_bw = c.fetchone()
        if max_bw[0] is None:
            max_bw = ['0']
	    # Get average bandwidth from IP
        c.execute('SELECT ROUND(AVG(bandwidth),2) FROM download WHERE ip = ? LIMIT 1', ip)
        avg_bw = c.fetchone()
        if avg_bw[0] is None:
            avg_bw = ['0']
	    # Get total number of downloads from IP
        c.execute('SELECT COUNT(ip) FROM download WHERE ip = ? LIMIT 1', ip)
        dl_count = c.fetchone()
        if dl_count[0] is None:
            dl_count = ['0']
        # Get number of times we have resolved to this IP
        c.execute('SELECT COUNT(ip) FROM dns WHERE ip = ? LIMIT 1', ip)
        dns_count = c.fetchone()
        # Get percentage this IP has been resolved
        c.execute('SELECT COUNT(ip) FROM dns')
        total_count = c.fetchone()
        percentage = dns_count[0] * 1.0 / total_count[0] * 100
        percentage = "%.2f" % percentage
        # Get the timestamp from the last resolution of this IP
        c.execute('SELECT timestamp FROM dns WHERE ip = ? ORDER BY timestamp DESC LIMIT 1', ip)
        last_resolve = c.fetchone()

        # Now insert into the 'output' table
        c.execute('INSERT INTO output VALUES(?,?,?,?,?,?,?,?,?)',
                  (ip[0], cdn[0], min_bw[0], max_bw[0], avg_bw[0], dl_count[0], dns_count[0],
                   percentage, last_resolve[0]))
        conn.commit()

    # write to CSV
    data = c.execute("SELECT * FROM output")
    with open('www/data/output.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerow(['IP Address', 'CDN', 'Min Throughput (Mbit/s)', 'Max Throughput (Mbit/s)',
                         'Avg Throughput (Mbit/s)', 'Number of Downloads', 'Number of Resolutions',
                         '% of Total Resolutions', 'Last Resolved'])
        writer.writerows(data)

    conn.close()
    return()


def status():
    """Generate statistics from DB and create CSV to be displayed on a webpage."""
    conn = sqlite3.connect('psn.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS status
    (COL1         TEXT,
    COL2          TEXT,
    COL3          TEXT,
    COL4          TEXT,
    COL5          TEXT,
    COL6          TEXT
    )''')

    # Delete the old values from 'status' table
    c.execute('DELETE FROM status')

    # Get total number of DNS resolutions across all IP addresses
    c.execute('SELECT COUNT(ip) FROM dns')
    total_dns = c.fetchone()

    # Get total number of downloads across all IP addresses
    c.execute('SELECT COUNT(*) FROM download')
    total_downloads = c.fetchone()

    # Average Akamai throughput
    c.execute('SELECT ROUND(AVG(bandwidth),2) FROM download WHERE cdn = "Akamai"')
    avg_akamai = c.fetchone()

    # Average Limelight throughput
    c.execute('SELECT ROUND(AVG(bandwidth),2) FROM download WHERE cdn = "Limelight"')
    avg_limelight = c.fetchone()

    # Average CloudFront throughput
    c.execute('SELECT ROUND(AVG(bandwidth),2) FROM download WHERE cdn = "CloudFront"')
    avg_cloudfront = c.fetchone()

    # Average Level3 throughput
    c.execute('SELECT ROUND(AVG(bandwidth),2) FROM download WHERE cdn = "Level3"')
    avg_level3 = c.fetchone()

    # Chance of Akamai IP
    c.execute('''SELECT ROUND(((SELECT CAST(COUNT(ip) AS FLOAT) FROM dns WHERE cdn = "Akamai")
    / (SELECT COUNT(ip) FROM dns) * 100),2) AS result''')
    chance_akamai = c.fetchone()

    # Chance of Limelight IP
    c.execute('''SELECT ROUND(((SELECT CAST(COUNT(ip) AS FLOAT) FROM dns WHERE cdn = "Limelight")
    / (SELECT COUNT(ip) FROM dns) * 100),2) AS result''')
    chance_limelight = c.fetchone()

    # Chance of CloudFront IP
    c.execute('''SELECT ROUND(((SELECT CAST(COUNT(ip) AS FLOAT) FROM dns WHERE cdn = "CloudFront")
    / (SELECT COUNT(ip) FROM dns) * 100),2) AS result''')
    chance_cloudfront = c.fetchone()

    # Chance of Level3 IP
    c.execute('''SELECT ROUND(((SELECT CAST(COUNT(ip) AS FLOAT) FROM dns WHERE cdn = "Level3")
     / (SELECT COUNT(ip) FROM dns) * 100),2) AS result''')
    chance_level3 = c.fetchone()

    # Recommended IP
    c.execute('''SELECT ip FROM (SELECT MAX(avg_bw),ip FROM output
    WHERE last_resolved > DATETIME("now", "-1 days"))''')
    recommended_ip = c.fetchone()

    # Get the current IP address configured in /etc/hosts if possible
    try:
        with open('/etc/hosts', 'r') as f:
            for line in f:
                host_ip = re.findall(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b.+(?=gs2)", line)
                if host_ip:
                    current_ip = host_ip[0].strip()
                else:
                    current_ip = 'None'
    except:
        current_ip = 'Unknown'

    c.execute('INSERT INTO status VALUES(?,?,?,?,?,?)',
              ('Number of DNS Lookups', total_dns[0], 'Avg. Akamai Throughput (Mbit/s)',
               avg_akamai[0], 'Chance of Akamai IP (%)', chance_akamai[0]))
    c.execute('INSERT INTO status VALUES(?,?,?,?,?,?)',
              ('Number of Download Tests', total_downloads[0],
               'Avg. CloudFront Throughput (Mbit/s)', avg_cloudfront[0],
               'Chance of CloudFront IP (%)', chance_cloudfront[0]))
    c.execute('INSERT INTO status VALUES(?,?,?,?,?,?)',
              ('Current Configured IP', current_ip, 'Avg. Level3 Throughput (Mbit/s)',
               avg_level3[0], 'Chance of Level3 IP (%)', chance_level3[0]))
    c.execute('INSERT INTO status VALUES(?,?,?,?,?,?)',
              ('Recommended IP', recommended_ip[0], 'Avg. Limelight Throughput (Mbit/s)',
               avg_limelight[0], 'Chance of Limelight IP (%)', chance_limelight[0]))
    conn.commit()

    data = c.execute("SELECT * FROM status")

    # write to CSV
    with open('www/data/status.csv', 'w') as f:
        writer = csv.writer(f)
        writer.writerows(data)

    conn.close()
    return()


def clean(json_delete):
    """Delete data from tables older than [n] days. Occasionally perform a VACUUM on the DB."""
    conn = sqlite3.connect('psn.db')
    c = conn.cursor()
    c.execute("DELETE FROM dns WHERE timestamp < DATETIME('now', ?)", [json_delete])
    c.execute("DELETE FROM download WHERE timestamp < DATETIME('now', ?)", [json_delete])

    chance = randint(1, 100)
    if chance == 1:
        c.execute("VACUUM")

    conn.commit()
    conn.close()
    return()


def main():
    """Parse arguments and initialise script."""

    parser = argparse.ArgumentParser(description='PSN Network Monitoring Tool')
    parser.add_argument('-s', '--dns', action='store_true',
                        help='Check PSN DNS resolution')
    parser.add_argument('-d', '--download', action='store_true',
                        help='Download file from PSN CDN providers')
    parser.add_argument('-c', '--config', action='store', required=True,
                        help='Location of the config file')

    args = parser.parse_args()
    config_location = args.config

    json_domain, json_path, json_size, json_delete = load_json(config_location)

    if args.dns is True:
        dns(json_domain)
    elif args.download is True:
        download(json_domain, json_path, json_size)
        create_csv()
        status()
        clean(json_delete)
    else:
        print "Please select an option"
        sys.exit(0)

if __name__ == '__main__':
    main()
