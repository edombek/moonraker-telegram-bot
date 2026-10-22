# moonraker-telegram-bot

![image](https://user-images.githubusercontent.com/51682059/140623765-3b839b4b-40c2-4f87-8969-6cb609f2c5f1.png)

The general idea of this project is to provide you with a way to control and monitor your printer without having to setup a vpn, opening your home network, or doing any sort of other network-related voodoo.
In addition, you get the benefits of push-style notifications always in your pocket, and a bandwidth-friendly way to check up on your print progress when not near the printer.

As always with solutions like these, we kindly remind you not to print unattended, and always to take all necessary precautions against fire hazards.

## Features and Installation:

Please check out our [wiki](https://github.com/nlef/moonraker-telegram-bot/wiki) for installation instructions and detailed feature descriptions.

## Telegram transport: HTTP Bot API or MTProto

By default the bot talks to Telegram over the HTTPS Bot API (`api.telegram.org`).
If that endpoint is blocked or throttled in your region, you can switch to the
MTProto protocol (a direct connection to Telegram data centers) by setting in
`telegram.conf`:

```ini
[bot]
api_type: mtproto
api_id: 123456
api_hash: 0123456789abcdef0123456789abcdef
```

`api_id` and `api_hash` are obtained from [my.telegram.org](https://my.telegram.org)
under API Development; the regular `bot_token` is used for the MTProto bot login.
No session files are stored - the bot re-authenticates with the token on every start.
The `http_proxy`/`socks_proxy` options apply to the HTTP transport only. For the
MTProto transport you can use an MTProxy server:

```ini
[bot]
mtproto_proxy: proxy-host:port:secret
```

The `secret` is the MTProxy secret as configured on the proxy: a plain 32-hex-char
secret, or a `dd`-prefixed one (randomized intermediate). The matching protocol is
picked automatically from the prefix. `socks_proxy`/`http_proxy` are ignored in
MTProto mode.

Note: MTProto bot login (`importBotAuthorization`) is rate-limited by Telegram.
Starting the bot repeatedly in short bursts can result in a `FloodWaitError`
and the bot will be unable to log in until the reported wait time has passed.
This does not affect normal single restarts.

Known MTProto limitation: Telegram rejects `messages.SendMultiMedia` for bot
sessions, so the MTProto transport sends album items (progress photo+text,
multi-file replies) one after another instead of as a single grouped album.
Everything else (photos, videos, documents, inline keyboards) is unaffected.

## Issues and bug reports

We will be happy to assist you with any issues that you have, as long as you can form a coherent sentence and are polite in your requests.
Please open an issue, and we will try our best to reproduce and fix it.
Feature requests and ideas are also more than welcome.

When writing issues/contacting for support please attach the 'telegram.log' as well as the output of `sudo journalctl -r -u moonraker-telegram-bot`.

### Happy Printing!
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/donate/?hosted_button_id=KCKKK5WLXNEFE)


---

**Klipper** by [KevinOConnor](https://github.com/KevinOConnor) :

https://github.com/Klipper3d/klipper

---
**Moonraker** by [Arksine](https://github.com/Arksine) :

https://github.com/Arksine/moonraker

---

**KIAUH - Klipper Installation And Update Helper** by [dw-0](https://github.com/dw-0) :

https://github.com/dw-0/kiauh

---

**Mainsail Webinterface** by [mainsail-crew](https://github.com/mainsail-crew) :

https://github.com/mainsail-crew/mainsail

---

**Fluidd Webinterface** by [fluidd-core](https://github.com/fluidd-core) :

https://github.com/fluidd-core/fluidd

---
