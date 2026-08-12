# Asset provenance

To the extent the repository owner holds copyright or other licensable rights,
the visual assets are included under the project-level MIT license in
`LICENSE`.

## OpenAI-generated editorial images

The ten PNG files in `web/public/assets/` contain embedded C2PA Content
Credentials. Their manifests identify:

- `gpt-image` version `2.0` as the software agent;
- `OpenAI Media Service API` as the claim generator; and
- `trainedAlgorithmicMedia` as the digital source type.

The manifests record creation on 2026-08-10. The files have been retained in
their original PNG form so that this provenance remains embedded. A visual
review found generic wardrobe and travel subjects with no visible third-party
logos, named people, or personal information.

All ten manifests were inspected with `c2patool` 0.27.10. It reported a
`Valid` validation state, matching assertion and asset hashes, and valid claim
signatures issued under the name `OpenAI OpCo, LLC`. The verifier's default
local trust store did not contain the signing or timestamp trust anchors, so
this record does not claim independent certificate-chain trust validation.

This provenance establishes how the final images were generated. It cannot,
by itself, prove that no third-party reference image was supplied as input.
The repository owner states that these were generated for this project using
ChatGPT and is responsible for ensuring any supplied inputs were authorized.

## Project mark and screenshots

`logo.svg` is the editable LoadOut project mark. `logo.png` and
`LoadOut-mark-preview.png` are Inkscape-rendered PNG versions of that mark.

The JPEG files in `assets/screenshots/` are captures of LoadOut running with
the repository's sanitized example catalog. They contain no real itinerary or
inventory records; their embedded editorial imagery is covered by the OpenAI
provenance described above.
