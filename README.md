# ES Scheduled Backup

A Frappe app that keeps a working copy of a site on a second server.

On a schedule, the source site backs up its database, pushes the backup and its
uploaded files to the destination server over SSH, and tells the destination to
restore it. The copy is a normal site that people can log in to, as old as the
last run.

It replaces the `replicate-to-clone.sh` cron script from the option C runbook
with a settings page, a Run Now button and a run log.

## What it needs

- Frappe v15 or v16 on both servers, with the same apps at the same versions.
- An SSH key on the source server that the destination accepts without a password.
- A restore script on the destination (below). The database admin password
  stays on the destination and is never stored in the source site.
- `rsync` and `ssh` on the source server.

## Install

Install on **both** benches, because the restored database expects the app's code.

```bash
cd ~/frappe-bench
bench get-app https://github.com/jamesvrempel/es_scheduled_backup
bench --site <site> install-app es_scheduled_backup
bench restart
```

## Set up the SSH connection

The source server pushes to the destination over SSH, so it needs a key the
destination accepts without a password prompt. The examples use
`192.168.0.221` as the destination's address and `enterprise` as the bench
user; replace them with your own.

Use the destination's **direct address** (its LAN IP, VPN IP or a public IP
that accepts SSH). Do not use the site's public hostname if the site is behind
Cloudflare or another web proxy: that name leads to the proxy, which does not
pass SSH, and the connection hangs with no error.

1. On the **source** server, as the bench user, create a key for this job:

    ```bash
    ssh-keygen -t ed25519 -f ~/.ssh/clone_sync -N ''
    ```

2. Install the public key on the destination. If the destination accepts
   password logins, this one command does it:

    ```bash
    ssh-copy-id -i ~/.ssh/clone_sync.pub enterprise@192.168.0.221
    ```

   If it answers `Permission denied (publickey)`, the destination only accepts
   keys, so add the key by hand:

    1. On the source, print the key and copy the **whole line**, including the
       `ssh-ed25519` at the start:

        ```bash
        cat ~/.ssh/clone_sync.pub
        ```

    2. On the **destination**, as the bench user, paste that line in place of
       `PASTE_THE_WHOLE_LINE_HERE`:

        ```bash
        mkdir -p ~/.ssh && chmod 700 ~/.ssh
        echo "PASTE_THE_WHOLE_LINE_HERE" >> ~/.ssh/authorized_keys
        chmod 600 ~/.ssh/authorized_keys
        ```

3. Back on the source, test it. Answer `yes` the first time, when asked whether
   to trust the host. It should print the destination's hostname with no
   password prompt:

    ```bash
    ssh -i ~/.ssh/clone_sync enterprise@192.168.0.221 hostname
    ```

## One-time setup on the destination

1. Create a MariaDB account that can run restores, with a random password
   stored in a file only the bench user can read. Paste the block as it is;
   there is nothing to fill in, and you never type the password:

    ```bash
    PASS=$(openssl rand -hex 20)
    sudo mariadb -e "CREATE OR REPLACE USER 'benchrestore'@'localhost' IDENTIFIED BY '$PASS'; GRANT ALL PRIVILEGES ON *.* TO 'benchrestore'@'localhost' WITH GRANT OPTION;"
    install -m 600 /dev/null ~/.restore-env
    echo "DB_ADMIN_PASSWORD='$PASS'" > ~/.restore-env
    unset PASS
    ```

   Check it. This should print `benchrestore@localhost`:

    ```bash
    source ~/.restore-env && mariadb -u benchrestore -p"$DB_ADMIN_PASSWORD" -e "SELECT CURRENT_USER();"
    ```

2. In the copy's `site_config.json`, add `"pause_scheduler": 1` and
   `"mute_emails": 1`, and set `encryption_key` to the source site's value.
   `bench restore` does not touch this file, so the settings survive every run.

   Every line needs a comma at the end except the last one before the closing
   `}`. Check the file after editing; this prints `VALID` when it is correct,
   or the line of the problem when it is not:

    ```bash
    python3 -m json.tool ~/frappe-bench/sites/erpclone.enterprisesystems.com.au/site_config.json > /dev/null && echo VALID
    ```

3. Create the restore script:

    ```bash
    mkdir -p ~/incoming
    cat > ~/restore-latest.sh <<'EOF'
    #!/bin/bash
    set -euo pipefail
    export PATH=/usr/local/bin:/usr/bin:/bin
    source /home/enterprise/.restore-env
    SITE=erpclone.enterprisesystems.com.au
    cd /home/enterprise/frappe-bench
    bench --site "$SITE" restore /home/enterprise/incoming/latest-database.sql.gz \
      --db-root-username benchrestore --db-root-password "$DB_ADMIN_PASSWORD" --force
    bench --site "$SITE" clear-cache
    echo "$(date -Is) restore complete"
    EOF
    chmod +x ~/restore-latest.sh
    ```

## Configure on the source site

Open the **ES Scheduled Backup** workspace from the sidebar or the apps screen.
It has shortcuts to Settings, the Run Log and Failed Runs, and a link to
Frappe's Download Backups page. The workspace is visible to System Managers.

Then open **Settings**.

| Setting | Meaning |
| --- | --- |
| Run on a schedule | Master switch for scheduled runs. Run Now works either way. |
| Source site | Runs only start when this matches the current site name. |
| How often | Hourly (at a chosen minute) or Nightly (at a chosen time). |
| Destination host | The destination's direct address, for example `192.168.0.221`. Not a hostname that goes through Cloudflare. |
| SSH user, port, key path | The login tested in "Set up the SSH connection". |
| Destination site | The copy's site name on the destination, for example `erpclone.enterprisesystems.com.au`. |
| Destination bench path | The bench folder on the destination. |
| Incoming backup file | Where the database backup lands on the destination. |
| Restore script | The script above. |
| Sync public / private files | Copy uploaded files with rsync. |
| Remove files deleted on the source | Keep the copy's files an exact match. |
| Restore the database on the destination | Turn off to ship backups without restoring. |
| Email me when a run fails | Sends the error to one address. |
| Keep log entries for (days) | Older log entries are removed daily. |

Then:

1. Save, and press **Test Connection**. It logs in to the destination and
   reports whether the site folder, incoming folder and restore script exist.
2. Press **Run Now** and follow the run in **View Log**.
3. Tick **Run on a schedule** once a manual run has succeeded.

A working Test Connection answers within a couple of seconds:

```
Connected to: <destination hostname>
OK      Site folder: ...
OK      Incoming folder: ...
OK      Restore script: ...
```

If you used the cron script before, remove its line from `crontab -e` so the
two do not both run.

## Troubleshooting the connection

| What you see | Cause | Fix |
| --- | --- | --- |
| Test Connection hangs, then reports no answer | Destination host is a name that leads to Cloudflare or another proxy | Set Destination host to the server's direct IP address |
| `Permission denied (publickey)` | The key is not in the destination's `authorized_keys`, or the line is incomplete | Repeat step 2 of "Set up the SSH connection"; the line must start with `ssh-ed25519` |
| `Host key verification failed` | The destination's host key changed, for example after a rebuild | On the source, run `ssh-keygen -R 192.168.0.221`, then the `ssh ... hostname` test again |
| `MISSING Restore script` or `MISSING Incoming folder` | Step 3 of the destination setup was not done | Create `~/incoming` and `~/restore-latest.sh` on the destination |
| `bash: !': event not found` when creating the database account | A `!` in a password typed inside double quotes | Use the random-password block above, which needs no typing |
| `ERROR 1396 ... CREATE USER failed` | The account already exists | The block above uses `CREATE OR REPLACE USER`, which handles this |
| `site_config.json is invalid` | A missing or extra comma | Run the `json.tool` check above and fix the line it names |

## How the schedule works

The Frappe scheduler checks every five minutes whether a scheduled moment has
passed, so a run starts within five minutes of the time you set. Switching the
schedule on, or changing it, never starts a run straight away; the first run is
at the next scheduled moment.

## Why the copy never runs the job itself

The copy's database is a restore of the source, so it holds these same
settings. Two things stop it acting on them: the copy's scheduler is paused,
and its site name does not match **Source site**. If you fail over to the copy
and want it to become the new source, change **Source site** and the
destination on the copy.

## License

MIT
