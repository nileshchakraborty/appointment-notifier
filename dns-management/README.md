# Local DNS management

Pi-hole is exposed only on the RPi LAN address. Open `http://rpi.local:3000`
and use **Local DNS > DNS Records** to add local hostnames.

OpenWA is reachable at `http://192.168.1.229/` and
`http://192.168.1.229:8081/`. For a managed hostname, add a record such as
`openwa.rpi.home.arpa` pointing to `192.168.1.229`; `.local` is reserved for
mDNS and is not reliably controlled by Pi-hole.
