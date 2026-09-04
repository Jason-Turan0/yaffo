# faces-and-people.md notes

## faces-review.webp
- The /faces?group_by=similarity&threshold=50 shot is usually stable: same faces, counts, filter values across runs.
- Observed run: diffuse rendering differences on left-sidebar controls only ("Assign Selected", "Group by", "Similarity Threshold", "Faces to analyze", "Apply Filters", "Clear Filters"). Content, counts, labels, filter values all unchanged. Classified environment_instability (non-reproducible renderer/anti-aliasing noise), >0.1% pixel delta so quarantined rather than promoted.
- No prose impact: caption and body text still accurate.
