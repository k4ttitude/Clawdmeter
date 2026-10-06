# Buddy sprites

`csb_splash_animations.h` is juppee's generated splash header from
https://github.com/juppeee/Clawdmeter/tree/csb-buddy at commit 10a8082. It holds
18 claudepix 20x20 animations: 3 DJ dances from claudepix.vercel.app, and 15
taken from Claude Session Browser (https://github.com/juppeee/claude-session-browser,
MIT), including the buddy states `done`, `think`, `write`, `allow` and `limit`.

`tools/import_buddy_sprites.py` turns four of them (`allow`, `limit`,
`expression surprise`, `expression sleep`) into `firmware/src/buddy_animations.h`.
They cover buddy states the official Clawd art has no close match for.
The Clawd character is Anthropic's; see the licensing note in the main README.
