# Phase 0: run the logger on EC2

Goal: the logger runs 24/7 on a small EC2 instance, and snapshots land in S3.
About 45 minutes the first time. Do the steps in order.

## 0. Accounts (in this order)

1. **511.org API token.** Request it first, because approval can take a while:
   https://511.org/open-data/token. Then email
   511sfbaydeveloperresources@googlegroups.com to ask for a higher rate limit.
   Say what the project is and the rate you want (for example, 1 request per
   30 s per feed). **Don't put the key in the email.**
2. **GitHub.** Create a public repo named `transit-predictor` (any name works)
   and push this folder. The server clones from it. `.gitignore` already
   blocks `.env`.
3. **AWS account.** https://aws.amazon.com. It needs a credit card. Right
   after signup:
   - Turn on MFA for the root user (IAM → Security credentials).
   - Create a budget: Billing → Budgets → "Monthly cost budget" at **$10**
     with an email alert. This is your safety net.
   - Stop using root for daily work. Create an admin login with IAM Identity
     Center, or an IAM user with MFA.
   - Use region **us-west-2 (Oregon)** for everything.
4. **healthchecks.io** (optional, free, recommended). Create a check with
   period 5 min and grace 15 min, then copy its ping URL. It emails you if
   logging stops. Without it, an outage can go unnoticed for weeks.

## 1. S3 bucket

S3 → Create bucket. Name it something like `<yourname>-transit-raw`
(bucket names are global). Region us-west-2. Leave "Block all public access"
**on**. Keep the other defaults.

## 2. IAM role for the server

This gives the server permission to write to the bucket without storing any
access keys on it.

IAM → Roles → Create role → Trusted entity: **AWS service → EC2**.
- Attach the managed policy **AmazonSSMManagedInstanceCore**. It lets you log
  in through the browser without SSH.
- Finish creating the role and name it `transit-logger-role`.
- Open the role, go to Add permissions → Create inline policy → JSON, and
  paste `deploy/iam-s3-policy.json` with your bucket name filled in. Name it
  `write-raw-snapshots`.

## 3. Launch the instance

EC2 → Launch instance:
- Name: `transit-logger`
- AMI: **Amazon Linux 2023**, architecture **64-bit (Arm)**
- Instance type: **t4g.micro** (about $6/month; free-tier credits cover it at first)
- Key pair: **Proceed without a key pair** (you'll use Session Manager)
- Network: create a security group with **no inbound rules** (remove the
  default SSH rule). Nothing on the internet can connect in.
- Storage: 16 GiB gp3
- Advanced details → IAM instance profile: `transit-logger-role`

Launch it, wait about 3 minutes, then go to Instance → **Connect → Session
Manager → Connect**. If Connect is greyed out, wait a few more minutes.

## 4. Install the logger (in the Session Manager shell)

```bash
sudo dnf install -y git
cd ~ && git clone https://github.com/<you>/transit-predictor.git && cd transit-predictor
sudo bash deploy/setup.sh          # first run creates /etc/transit-logger.env and stops
sudo nano /etc/transit-logger.env  # set API_511_KEY, S3_BUCKET, HEALTHCHECK_URL
sudo bash deploy/setup.sh          # second run starts everything
```

## 5. Verify (do not skip)

```bash
journalctl -u transit-logger -n 30        # "saved N bytes" lines, no repeated warnings
sudo systemctl start transit-upload.service && journalctl -u transit-upload -n 20
aws s3 ls s3://<bucket>/raw/ --recursive | tail
```

Next, check the AC Transit agency code. If the logs show HTTP errors for the
511 feeds, run:

```bash
curl -s "https://api.511.org/transit/gtfsoperators?api_key=$KEY&format=json" | head -c 2000
```

Find AC Transit's `Id` in the output, set `AGENCY_511` to it, and run
`sudo systemctl restart transit-logger`.

Finally, test the restart behaviour: `sudo reboot`, reconnect, and check the
logger came back up with `systemctl status transit-logger`.

## Updating later

```bash
cd ~/transit-predictor && git pull && sudo bash deploy/setup.sh
```

## Daily health check (30 seconds)

Check that healthchecks.io is green. Each day folder in S3 should grow at
roughly the same rate every day. A sudden drop means feed or logger trouble.
