# macOS Recovery: reverse SSH when USB input fails

Use when the user wants SSH instead of Pi input and authorizes networking on the target. HDMI capture can still work while USB input fails. This route worked on Apple Silicon Recovery, macOS 27.0 build 26A428, on 2026-11-08. Recheck addresses, devices, available commands and credentials each session.

## 1. Controller endpoint and outbound tunnel

The Windows controller already had OpenSSH Server (`sshd`) running automatically on TCP 22 and an enabled inbound firewall rule. Use its LAN address and your account name where the commands below show `<controller-address>` and `<user>`. Verify the current listener and reachability before installing another server or changing firewall rules.

On the Mac, in Recovery Terminal:

```sh
ssh -o ExitOnForwardFailure=yes -NR 9000:localhost:22 <user>@<controller-address>
```

The user enters the Windows account password on the Mac (not the Windows PIN). Keep this terminal running and open a second Recovery Terminal for setup. Use an available port if 9000 is occupied. Bind the forwarded port to loopback; do not expose it on all interfaces.

Check the controller's port 9000 and read the SSH greeting through it. A listener proves the tunnel was accepted. A connection that closes without an SSH greeting does **not** prove remote command access: the Mac's own SSH server still needs to be started. `ExitOnForwardFailure` does not test that destination service.

## 2. Recovery SSH server and missing tools

`/usr/sbin/sshd -t` reported **no hostkeys available**. The subsequent bootstrap failed at `ssh-keygen`: Recovery did not contain it. This Recovery did have `ssh`, `scp`, `sshd`, `find`, `stat` and `plutil`; it lacked `du`, `python3`, `sqlite3` and `gzip`. Check with `command -v` instead of assuming the normal macOS toolset is available. When a script fails, identify its exact failing command before replacing the transfer mechanism: `scp` worked here.

Prepare the bootstrap **on the controller**, using a fresh temporary host key generated there and the public half of an existing controller login key. Keep the controller's login private key on the controller. Never commit or log the generated host private key, passwords, or a bootstrap containing private key material.

On Windows PowerShell, an explicit `cmd.exe /d /c` invocation preserved `ssh-keygen -N ""`; empty native arguments can be lost with older PowerShell argument passing. Use a fresh, checked temporary path and verify key generation succeeds before assembling the bootstrap.

The bootstrap that worked:

1. Require root and `set -eu`.
2. Create a fresh directory with `mktemp -d /var/root/keypi-ssh.XXXXXX` and mode 700. This is inside Recovery, not the mounted installed Data volume.
3. Write the generated host private key as `hostkey` and the controller's **public** login key as `authorized_keys`, both mode 600. Use literal quoted heredocs or equivalent safe file writes; preserve LF line endings.
4. Write this configuration, substituting the generated directory for `RECOVERY_DIR`:

   ```text
   ListenAddress 127.0.0.1
   Port 22
   HostKey RECOVERY_DIR/hostkey
   PidFile RECOVERY_DIR/sshd.pid
   AuthorizedKeysFile RECOVERY_DIR/authorized_keys
   UsePAM no
   PubkeyAuthentication yes
   AuthenticationMethods publickey
   PasswordAuthentication no
   KbdInteractiveAuthentication no
   PermitRootLogin prohibit-password
   AllowUsers root
   AllowTcpForwarding no
   AllowAgentForwarding no
   X11Forwarding no
   PermitTunnel no
   LogLevel VERBOSE
   ```

5. Validate and start the daemon:

   ```sh
   /usr/sbin/sshd -t -f "$work/sshd_config"
   /usr/sbin/sshd -f "$work/sshd_config" -E "$work/sshd.log"
   ```

6. Print success, the host fingerprint computed on the controller, the log path and the PID-file path. Do not invoke `ssh-keygen` on the Mac even for the final fingerprint display.

The generated bootstrap was placed outside the skills repo at the controller's `~/.ssh/mac-recovery.sh`. The user needed one command in the second Mac Terminal:

```sh
scp <user>@<controller-address>:.ssh/mac-recovery.sh /tmp/k.sh && sh /tmp/k.sh
```

Prepare a dedicated controller `known_hosts` file from the generated host public key, with host field `[127.0.0.1]:9000`. Then connect using the matching controller login private key, `IdentitiesOnly=yes`, `BatchMode=yes`, `StrictHostKeyChecking=yes`, and `UserKnownHostsFile` pointing to that file:

```sh
ssh -T -p 9000 -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=~/.ssh/mac-recovery-known_hosts root@127.0.0.1 'id; sw_vers; diskutil info /'
```

Use expanded native paths on Windows. Confirm `macOS Base System` as the root volume; a successful `id` as root and read-only command execution establish that the route works.

## 3. Unlock and identify the installed Data volume

Recovery's `/System/Volumes/Data` is a temporary filesystem, not the installed user's data. Inspect `diskutil apfs list` and `/Volumes` first. The installed Data volume was `disk3s5`, mounted after unlocking at `/Volumes/Macintosh HD - Data`; identifiers may change next boot.

If FileVault is locked, the user can run this in the second Mac Terminal with the currently verified device identifier:

```sh
diskutil apfs unlockVolume disk3s5
```

The user enters the Mac password locally. If automating credential entry instead, obtain it through the available secure secret tool and keep it out of command arguments and output. Verify the Data volume is mounted and unlocked before inspecting installed files.

Without `du`, use `find` and `stat -f '%z|%b|%N'` to enumerate file sizes and allocated blocks, and aggregate on the controller. `plutil -p` and `plutil -extract KEY raw -o - FILE` worked for runtime metadata. Measure filesystem free space before and after with `df -k`.

## 4. Completed example and scope

This is a historical result, **not** an instruction to repeat deletion:

- The earlier user instruction was to delete only the old iOS 18.2 folder. Its `Info.plist` confirmed `SimulatorVersion=18.2`, `Build=22C150`.
- Exact removed asset, relative to the installed Data volume: `System/Library/AssetsV2/com_apple_MobileAsset_iOSSimulatorRuntime/3ee34d773407290b674484df946712d331558e17.asset`.
- The runtime DMG was 8,994,719,232 bytes. Actual free-space increase was about 9.0 GB; free space reached about 37 GB.
- iOS 27, tvOS 27 and simulator caches were preserved. An additional roughly 3.8 GB cache matching iOS 18.2 was observed but was outside that single-folder deletion scope.
- Before removal, verify the intended volume UUID, resolve the exact target path, reject symlink targets, and match the runtime version/build. Avoid wildcard deletions across simulator assets. Verify absence and free-space change afterward.

For future cleanup, establish the current target and authorization from the user's request; this example does not authorize a new deletion. Do not disable SIP merely because the asset is marked `restricted`: the exact folder removal succeeded from Recovery without changing SIP settings.

## 5. End the temporary session

Stop the temporary daemon using its PID file when finished, or reboot into normal macOS when that is part of the requested work. Reboot ends the reverse tunnel and Recovery's temporary files. Remove generated bootstrap/private-host-key copies and any temporary context/database copies from the controller. Keep ordinary controller login keys intact. Do not store credentials or generated private keys in this reference.
