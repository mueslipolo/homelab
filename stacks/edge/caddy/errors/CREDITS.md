# Error page art

The scenes in `art/*.b64` are PNGs (600 px wide, F5 160 px), base64-encoded so
the template can put them inside the page.

| File      | Status        | Scene                                    | Source                              |
| --------- | ------------- | ---------------------------------------- | ----------------------------------- |
| `502.b64` | 502, 503      | "This is fine": coffee in a room on fire | Free Gophers Pack, illustrations/2  |
| `504.b64` | 504           | asleep on a laptop                       | Free Gophers Pack, illustrations/44 |
| `404.b64` | 404           | lost among map pins                      | Free Gophers Pack, illustrations/24 |
| `500.b64` | anything else | next to a lit bomb                       | Free Gophers Pack, illustrations/37 |
| `f5.b64`  | 502, 504      | "I love F5"                              | Free Gophers Pack, illustrations/41 |

- Drawings: Maria Letta, [Free Gophers Pack](https://github.com/MariaLetta/free-gophers-pack),
  CC0 (public domain).
- The Go gopher: created by Renee French, licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (go.dev/brand).
  The pages' footer carries the credit.

Another scene: take a PNG from the pack, shrink it, then
`base64 -w 76 scene.png > art/<status>.b64`.
