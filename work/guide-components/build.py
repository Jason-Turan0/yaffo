from pathlib import Path
import base64,json
folder=Path(__file__).resolve().parent
repo=folder.parents[1]
assets={
'detail':'docs/guide/library-basics/assets/photo-details/media-detail.webp',
'faces':'docs/guide/organize-review/assets/assigning-faces/faces-assign-02-controls.webp',
'import':'docs/guide/create-customize/assets/importing/new-import-sync.webp',
'import-options':'docs/guide/create-customize/assets/importing/import-folder-options.webp',
'import-destination':'docs/guide/create-customize/assets/importing/import-destination.webp',
'import-preview':'docs/guide/create-customize/assets/importing/import-preview.webp',
'library':'docs/guide/start-here/assets/getting-started/gallery-home.webp',
'change':'docs/guide/start-here/assets/ask-yaffo/ask-yaffo-change-card.webp',
}
encoded={k:'data:image/webp;base64,'+base64.b64encode((repo/v).read_bytes()).decode() for k,v in assets.items()}
s=(folder/'template.html').read_text().replace('/*ASSETS*/',json.dumps(encoded))
(folder/'index.html').write_text(s)
print('Created component gallery:',(folder/'index.html').stat().st_size,'bytes; all assets embedded')
