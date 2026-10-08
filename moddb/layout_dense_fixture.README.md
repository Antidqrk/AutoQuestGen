The numeric `layout_dense_fixture.json` is an anonymized regression fixture
from AutoQuestGen's own generated recipe graph and its previous generated
placement. It contains 111 node positions and parent indices, plus a stable
test seed. It does not contain an authored modpack chapter arrangement,
quest prose, item identifiers, icons, or other modpack artwork.

The layout test preserves the graph while checking that crossing reduction
does not introduce overlaps, oversized maps, or excessive edge lengths.
