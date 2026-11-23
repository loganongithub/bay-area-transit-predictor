# Phase 0: run the logger on EC2

Goal: the logger runs 24/7 on a small EC2 instance and snapshots land in S3.
Everything after account setup is scripted from your laptop. No console
clicking, no SSH.

## 1. Accounts and tools (you, in this order)

1. **511.org API token.** Request one at https://511.org/open-data/token. When
   it arrives, copy `.env.example` to `.env` and set `API_511_KEY`.
   - For a higher rate limit, join https://groups.google.com/g/511sfbaydeveloperresources
     first, then email the group. Posts from non-members bounce.
2. **AWS account.** Sign up at https://aws.amazon.com (needs a credit card).
   Then, in the console:
   - Turn on MFA for the root user: account menu → Security credentials.
   - Create a day-to-day admin login. IAM → Users → Create user, tick
     "Provide user access to the AWS Management Console", attach
     `AdministratorAccess`, then turn on MFA for that user. Use this user from
     now on, not root.
3. **AWS CLI on Windows.** Install it:
   ```powershell
   winget install -e --id Amazon.AWSCLI
   ```
   Open a new terminal, then sign in as the admin user:
   ```powershell
   aws login
   ```
   If your CLI version has no `aws login`, use the fallback: create an access
   key for the admin user (IAM → user → Security credentials → Create access
   key → CLI), then run `aws configure` and paste it in. Never put that key in
   the repo. Check it worked: `aws sts get-caller-identity`.
4. **healthchecks.io** (optional, free). Create a check with a 5-minute period
   and 15-minute grace, then copy its ping URL. It emails you if logging stops.

## 2. Build the server (one command)

Windows blocks local scripts by default, so each command below uses
`-ExecutionPolicy Bypass` for that run only. It doesn't change any system
setting.

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\provision.ps1 -Bucket <yourname>-transit-raw -AlertEmail <your email> -HealthcheckUrl <ping url>
```

What it creates in us-east-2 (Ohio; this AWS account only allows that region). It's safe to re-run, because existing pieces are
reused:

| Resource | Why |
|---|---|
| S3 bucket, all public access blocked | The archive |
| IAM role `transit-logger-role` | Lets the server write to `raw/` in that bucket, read its 511 key, and use Session Manager. Nothing else. |
| Security group `transit-logger`, no inbound rules | Nothing on the internet can connect to the server |
| EC2 `t4g.micro`, Amazon Linux 2023 ARM, 16 GB disk | Runs `user-data.sh` on first boot: clones this repo, writes config, runs `setup.sh` |
| Budget `transit-monthly` | Emails you when spending passes $8 of a $10 month |

Cost: about $6/month for the instance, about $3.60/month for its public IPv4
address, and cents for S3. New-account credits cover this at first.

## 3. Add the 511 key

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\set-511-key.ps1
```

This stores the key from `.env` in SSM Parameter Store, encrypted. The server
copies it into its config and restarts the logger. Re-run it whenever the
key changes.

## 4. Verify (do not skip)

Wait about 5 minutes after provisioning, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\status.ps1 -Bucket <yourname>-transit-raw
```

You should see:
- `active` twice: the logger and the upload timer.
- Recent `saved N bytes` lines for `bart_tripupdates`. After step 3 you should
  also see them for `511_ac_tripupdates` and `511_ac_vehiclepositions`.
- Today's S3 counts going up. The first upload runs about 10 minutes after
  boot.

If the 511 feeds show HTTP errors, the AC Transit agency code may not be
`AC`. Look it up:
`https://api.511.org/transit/gtfsoperators?api_key=<key>&format=json`, or ask
Claude to.

Reboot test: `aws ec2 reboot-instances --instance-ids <id> --region us-east-2`.
Run `status.ps1` again a few minutes later. Everything should be `active`
again without you touching anything.

## Logging in to the server (rarely needed)

EC2 console → instance → Connect → Session Manager. The bootstrap log is at
`/var/log/transit-bootstrap.log`.

## Updating the logger

Push the change to GitHub, then on the server (Session Manager):

```bash
cd /opt/src && sudo git pull && sudo bash deploy/setup.sh
```

## Daily health check (30 seconds)

Check that healthchecks.io is green, or run `status.ps1`. Day-to-day snapshot
counts should be about the same. A sudden drop means feed or logger trouble.
