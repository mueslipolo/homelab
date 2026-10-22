#!/usr/bin/env python3
"""Writes ntfy.rsc (next to this file) from the plain RouterOS script sources
below, so the quoting inside `source="..."` is done by code, not by hand.
Edit the sources here, then: python3 tools/mikrotik/ntfy-rsc.py"""
from pathlib import Path

SEND = r'''# Push one message to ntfy. Call it as a function:
#   :local send [:parse [/system script get ntfy-send source]]
#   $send title="..." message="..." tags="..." priority="..."
:local url "https://ntfy.home.example.com/homelab"
:local token "tk_REPLACE_ME"
# /tool fetch separates header fields with commas: none in the title.
:local t ""
:if ([:len $title] > 0) do={
  :for i from=0 to=([:len $title] - 1) do={
    :local c [:pick $title $i]
    :if ($c = ",") do={ :set c ";" }
    :set t ($t . $c)
  }
}
:local hdr ("Authorization: Bearer " . $token . ",Title: " . $t)
:if ([:len $tags] > 0) do={ :set hdr ($hdr . ",Tags: " . $tags) }
:if ([:len $priority] > 0) do={ :set hdr ($hdr . ",Priority: " . $priority) }
:do {
  /tool fetch url=$url http-method=post http-header-field=$hdr http-data=$message output=none check-certificate=yes
} on-error={ :log warning ("ntfy: could not send: " . $t) }
'''

DHCP = r'''# A device got a lease that is not a static lease: push it once per MAC
# until the next reboot, and name leases after their host name. Called by the
# DHCP server's lease-script with
# bound= mac= ip= host= and optionally where= (see the end of ntfy.rsc):
# "LAN" by default, "Wi-Fi" on an access point with its own DHCP.
:global ntfySeenMacs
:local w "LAN"
:if ([:len $where] > 0) do={ :set w $where }
:if ([:typeof $ntfySeenMacs] != "array") do={ :set ntfySeenMacs [:toarray ""] }
:if ([:tostr $bound] = "1") do={
  # Static leases are the known devices.
  :local known false
  :foreach l in=[/ip dhcp-server lease find where mac-address=$mac] do={
    :if (![/ip dhcp-server lease get $l dynamic]) do={ :set known true }
  }
  :if (!$known) do={
    :if ([:typeof ($ntfySeenMacs->$mac)] = "nothing") do={
      :set ($ntfySeenMacs->$mac) true
      :local name $host
      # Android sends its host name only in a later DHCP message: the lease
      # record has it a moment after this script starts. Then its vendor class
      # (e.g. android-dhcp-14) says at least what it is.
      :if ([:len $name] = 0) do={
        :delay 5s
        :foreach l in=[/ip dhcp-server lease find where mac-address=$mac] do={
          :local h [/ip dhcp-server lease get $l host-name]
          :if ([:len $h] > 0) do={ :set name $h }
          :if ([:len $name] = 0) do={
            :do { :set name [/ip dhcp-server lease get $l class-id] } on-error={}
          }
        }
      }
      :if ([:len $name] = 0) do={ :set name "(no host name)" }
      :local send [:parse [/system script get ntfy-send source]]
      # Visitors come and go: no "make it static" advice for a guest network.
      :local tip "\nKnown device? Make its lease static and it won't alert again."
      :if ($w ~ "guest") do={ :set tip "" }
      $send title=("New device on the " . $w . ": " . $name) message=($name . "\nMAC " . $mac . "\nIP " . $ip . $tip) tags="eyes" priority="high"
    }
  }
  # Every device, known or new: a lease without a comment gets its host name
  # as one, so the lease list says what each device is. A comment you wrote
  # yourself is never touched.
  # A lease that never had a comment has none, not an empty one, and
  # `find where comment=""` does not match it: check each lease instead.
  :local unnamed false
  :foreach l in=[/ip dhcp-server lease find where mac-address=$mac] do={
    :if ([:len [/ip dhcp-server lease get $l comment]] = 0) do={ :set unnamed true }
  }
  :if ($unnamed) do={
    :delay 6s
    :foreach l in=[/ip dhcp-server lease find where mac-address=$mac] do={
      :local h [/ip dhcp-server lease get $l host-name]
      :if ([:len [/ip dhcp-server lease get $l comment]] = 0 && [:len $h] > 0) do={
        :do { /ip dhcp-server lease set $l comment=$h } on-error={}
      }
    }
  }
}
'''

INET = r'''# Netwatch up/down for the hosts commented ntfy-inet-*. "Internet down" only
# when every one of them is down; "back up" with how long it lasted.
# Called with host= state=up|down. While the internet is down, ntfy on the
# LAN still delivers to Android phones on the Wi-Fi; iPhones need ntfy.sh.
:global ntfyInetDownAt
:local send [:parse [/system script get ntfy-send source]]
:if ($state = "down") do={
  :local allDown true
  :foreach n in=[/tool netwatch find where comment~"^ntfy-inet"] do={
    :if ([/tool netwatch get $n status] != "down") do={ :set allDown false }
  }
  :if ($allDown && [:typeof $ntfyInetDownAt] != "time") do={
    :set ntfyInetDownAt [:timestamp]
    $send title="Internet down" message="No answer from any of the netwatch hosts." tags="rotating_light" priority="high"
  }
}
:if ($state = "up" && [:typeof $ntfyInetDownAt] = "time") do={
  :local lasted ([:timestamp] - $ntfyInetDownAt)
  :set ntfyInetDownAt ""
  $send title="Internet back" message=("Down for " . [:tostr $lasted]) tags="white_check_mark"
}
'''

WG = r'''# Every minute: a WireGuard peer whose last handshake is under 3 min old is
# connected; one with none for 10 min after that has gone quiet. Pushes the
# transitions only. Named by comment, else name, else start of its key.
:global ntfyWgUp
:if ([:typeof $ntfyWgUp] != "array") do={ :set ntfyWgUp [:toarray ""] }
:local send [:parse [/system script get ntfy-send source]]
:foreach p in=[/interface wireguard peers find] do={
  :local key [:tostr $p]
  :local name [/interface wireguard peers get $p comment]
  :if ([:len $name] = 0) do={ :do { :set name [/interface wireguard peers get $p name] } on-error={} }
  :if ([:len $name] = 0) do={ :set name [:pick [/interface wireguard peers get $p public-key] 0 8] }
  :local hs [/interface wireguard peers get $p last-handshake]
  :local recent false
  :local gone true
  :if ([:typeof $hs] = "time") do={
    :if ($hs < 3m) do={ :set recent true }
    :if ($hs < 10m) do={ :set gone false }
  }
  :local was false
  :if ([:typeof ($ntfyWgUp->$key)] = "bool") do={ :set was ($ntfyWgUp->$key) }
  :if ($recent && !$was) do={
    :set ($ntfyWgUp->$key) true
    $send title=("VPN: " . $name . " connected") message=("WireGuard peer " . $name . " is connected.") tags="key" priority="low"
  }
  :if ($gone && $was) do={
    :set ($ntfyWgUp->$key) false
    $send title=("VPN: " . $name . " gone quiet") message=("No handshake from " . $name . " for 10 minutes.") tags="zzz" priority="low"
  }
}
'''


def esc(src):
    lines = src.rstrip("\n").split("\n")
    out = []
    for i, line in enumerate(lines):
        e = line.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("?", "\\?")
        prefix = "" if i == 0 else "    \\n"
        suffix = "\\r\\" if i < len(lines) - 1 else '"'
        out.append(prefix + e + suffix)
    return 'source="' + "\n".join(out)


def script(name, src):
    return (f"/system script add name={name} policy=read,write,policy,test dont-require-permissions=no "
            + esc(src) + "\n")


HEADER = r'''# ntfy notifications from a MikroTik router (RouterOS 7): a new device on the
# LAN, internet down/back, WireGuard peers connecting. Pushes to the homelab
# topic through Caddy. Setup: docs/guides/11-notifications.md, "MikroTik".
#
# Before importing, replace in this file:
#   ntfy.home.example.com   your ntfy hostname (resolvable by the router)
#   tk_REPLACE_ME           the ntfy publish token
# Then upload it (Files, or scp) and: /import ntfy.rsc
# Re-importing replaces everything below; nothing else is touched.
#
# The router checks ntfy's certificate (check-certificate=yes): import Let's
# Encrypt's roots once, see the guide. Generated by ntfy-rsc.py: edit that.

/system script remove [find where name~"^ntfy-"]
/system scheduler remove [find where name~"^ntfy-"]
/tool netwatch remove [find where comment~"^ntfy-"]

# ---- ntfy-send: the one place with the URL and token
'''

NETWATCH = r'''
# ---- internet: two public resolvers, so one of them failing is not an outage
/tool netwatch add host=1.1.1.1 type=icmp interval=30s comment=ntfy-inet-1 \
    up-script=":local f [:parse [/system script get ntfy-inet source]]; \$f host=1.1.1.1 state=up" \
    down-script=":local f [:parse [/system script get ntfy-inet source]]; \$f host=1.1.1.1 state=down"
/tool netwatch add host=9.9.9.9 type=icmp interval=30s comment=ntfy-inet-2 \
    up-script=":local f [:parse [/system script get ntfy-inet source]]; \$f host=9.9.9.9 state=up" \
    down-script=":local f [:parse [/system script get ntfy-inet source]]; \$f host=9.9.9.9 state=down"

# ---- WireGuard: checked every minute
/system scheduler add name=ntfy-wg interval=1m start-time=startup policy=read,write,policy,test \
    on-event="/system script run ntfy-wg"

# ---- new devices: attach the lease script to your LAN DHCP server(s). Not done
# here, because it replaces any lease-script already set. Run, with your
# server's name (/ip dhcp-server print):
#
# /ip dhcp-server set [find name=defconf] lease-script=":local f [:parse [/system script get ntfy-dhcp source]]; \$f bound=\$leaseBound mac=\$leaseActMAC ip=\$leaseActIP host=\$\"lease-hostname\""
#
# On a Wi-Fi access point with its own DHCP, add where="Wi-Fi" at the end of
# that lease-script, so its alerts say "New device on the Wi-Fi".
#
# The lease-script also names each lease after its host name (as a comment)
# from the next time the device connects. To name the existing ones at once:
#
# /ip dhcp-server lease { :foreach l in=[find where host-name!=""] do={ :if ([:len [get $l comment]] = 0) do={ set $l comment=[get $l host-name] } } }
'''

out = HEADER + script("ntfy-send", SEND) + "\n# ---- ntfy-dhcp: called by the DHCP server's lease-script\n" + script("ntfy-dhcp", DHCP) \
    + "\n# ---- ntfy-inet: called by netwatch\n" + script("ntfy-inet", INET) \
    + "\n# ---- ntfy-wg: run by the scheduler\n" + script("ntfy-wg", WG) + NETWATCH
(Path(__file__).parent / "ntfy.rsc").write_text(out)
