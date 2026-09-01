# When peers refuse each other over the clock

## The symptom

A pull or push refuses with something like:

```
request is 246s out of date: the two machines' clocks disagree by more
than 120s. Sync the clock on both and retry.
```

Nothing is wrong with flanner, the network, or the entitlement. The two
machines disagree about what time it is.

## Why the check exists

Every request between devices is signed and carries the moment it was made.
The receiver compares that moment against its own clock and refuses anything
older than `MAX_SKEW`, currently two minutes (`flanner/device_auth.py`).

This is replay protection. A signature alone cannot catch a copied request:
the copy is genuine, just old. Only the timestamp reveals it. Without a
window, a captured request would be valid forever.

## Timezones are not the problem

The sender converts to UTC before stamping:

```python
moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
issued_at = moment.isoformat().replace("+00:00", "Z")
```

Nairobi and Tokyo at the same instant produce the same stamp. Their wall
clocks differ by six hours; the instant does not. Verified: the two parse to
a difference of 0.0 seconds.

The only timezone-shaped hazard is a naive timestamp. `verify_request`
assumes UTC when no offset is attached, so a sender emitting bare local time
would look hours off. That cannot happen from this client — the sender always
attaches `Z`, and there are no naive `datetime.now()` calls in the package.

## Fixing a drifted Windows machine

`w32tm /resync /force` reporting **"the computer did not resync because no
time data was available"** usually means the Windows Time service is not
running, so there is no source to ask.

As Administrator:

```
net start w32time
w32tm /config /manualpeerlist:"pool.ntp.org,0x9 time.windows.com,0x9" /syncfromflags:manual /reliable:yes /update
w32tm /resync /force
```

If the service refuses to start, re-register it:

```
w32tm /unregister && w32tm /register && net start w32time
```

Manual start means it stops at reboot and the drift returns. To keep it:

```
sc config w32time start= auto
```

The space after `start=` is `sc` syntax, not a typo.

Confirm with `w32tm /query /status` — look for a real Source and a recent
Last Successful Sync Time.

Some networks block NTP, which is UDP port 123. Hotel and office wifi often
do. Then set the clock by hand; the two machines only need to agree within
two minutes, not to be accurate.

## Worth deciding

Two minutes is tight. Laptops that sleep and virtual machines both drift, and
this window is the first thing a new user meets when it happens. AWS SigV4
and OAuth both allow five.

Widening it is a real trade: a longer window is a longer period in which a
captured request still works. Left at two deliberately, and noted here rather
than changed quietly.

Both machines in the 2026-09-01 two-device test had `w32time` stopped with
start type `Manual`, so neither was correcting itself. That is likely the
default state on a fresh Windows install, which makes this a first-run
problem rather than an edge case.
