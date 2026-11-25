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

## One-time setup on the destination

1. Create a MariaDB account that can run restores, and store its password in a
   file only the bench user can read:

    ```bash
    sudo mariadb -e "CREATE USER 'benchrestore'@'localhost' IDENTIFIED BY '<RESTORE_PASS>'; GRANT ALL PRIVILEGES ON *.* TO 'benchrestore'@'localhost' WITH GRANT OPTION;"
    install -m 600 /dev/null ~/.restore-env
    echo "DB_ADMIN_PASSWORD='<RESTORE_PASS>'" > ~/.restore-env
    ```

2. In the copy's `site_config.json`, add `"pause_scheduler": 1` and
   `"mute_emails": 1`, and set `encryption_key` to the source site's value.
   `bench restore` does not touch this file, so the settings survive every run.

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

Open **ES Scheduled Backup Settings** (search for it, or use the tile on the
apps screen).

| Setting | Meaning |
| --- | --- |
| Run on a schedule | Master switch for scheduled runs. Run Now works either way. |
| Source site | Runs only start when this matches the current site name. |
| How often | Hourly (at a chosen minute) or Nightly (at a chosen time). |
| Destination host, SSH user, port, key path | How to reach the destination. |
| Destination site, bench path | Where the copy lives. |
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

If you used the cron script before, remove its line from `crontab -e` so the
two do not both run.

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
