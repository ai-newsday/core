from __future__ import annotations
import hashlib
import numpy as np
from src.core.types import RawItem, NewsItem, Cluster, DedupConfig, RunContext, SourceType, DedupResult
from src.core.registry import load_source_priorities
from src.observability.events import emit


def build_embed_text(item: RawItem) -> str:
    """title_en + summary; degrade to title only when summary missing (spec §5.1)."""
    return f"{item.title_en}\n{item.raw_summary}" if item.raw_summary else item.title_en


def embedding_id(link: str) -> str:
    """Stable 16-hex id derived from the item link (spec §5.2)."""
    return hashlib.sha256(link.encode()).hexdigest()[:16]


def _cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _rank_index(source_type: SourceType, order: list[str]) -> int:
    try:
        return order.index(source_type.value)
    except ValueError:
        return len(order)            # unknown types sort last


def cluster(items: list[RawItem],
            vectors: list[list[float] | None],
            priority_of: dict[str, int],
            config: DedupConfig,
            ctx: RunContext) -> list[Cluster]:
    """Pure greedy-threshold clustering. `vectors` aligns to `items` by index;
    None means that item has no embedding and is forced into its own singleton.
    Seeds are primaries by construction because of the priority sort (spec §5)."""
    indexed = [
        (it, vectors[i], embedding_id(it.link))
        for i, it in enumerate(items)
    ]
    indexed.sort(key=lambda t: (
        _rank_index(t[0].source_type, config.source_type_rank),
        priority_of.get(t[0].source, 3),
        t[0].published_at,
    ))

    open_clusters: list[dict] = []
    for it, vec, emb_id in indexed:
        joined = False
        if vec is not None:
            best_sim, best = -1.0, None
            for oc in open_clusters:
                if oc["seed_vec"] is None:
                    continue
                sim = _cosine(vec, oc["seed_vec"])
                if sim > best_sim:
                    best_sim, best = sim, oc
            if best is not None and best_sim > config.similarity_threshold:
                best["members"].append((it, emb_id))
                joined = True
        if not joined:
            open_clusters.append(
                {"seed_vec": vec, "seed_item": it, "seed_emb": emb_id,
                 "members": [(it, emb_id)]})

    clusters: list[Cluster] = []
    for n, oc in enumerate(open_clusters, start=1):
        cid = f"evt-{ctx.now:%Y-%m-%d}-{n:03d}"
        seed_item = oc["seed_item"]
        members = [m for m, _ in oc["members"]]
        related = [m.link for m in members if m is not seed_item]
        primary = NewsItem(**seed_item.model_dump(), cluster_id=cid,
                           related_links=related, embedding_id=oc["seed_emb"])
        clusters.append(Cluster(cluster_id=cid, primary=primary,
                                members=members, related_links=related,
                                size=len(members)))
    return clusters


def _unique_by_link(items: list[RawItem]) -> list[RawItem]:
    """Collapse exact-link duplicates (keep first), preserving order.
    Upstream collect() concatenates sources without link-dedup, so identical
    links can arrive; embedding_id = sha256(link) would otherwise collide and
    silently drop a vector from the by_emb map below."""
    seen: set[str] = set()
    out: list[RawItem] = []
    for it in items:
        if it.link not in seen:
            seen.add(it.link)
            out.append(it)
    return out


def dedup(items: list[RawItem], config: DedupConfig, ctx: RunContext, *,
          embedder, store) -> DedupResult:
    emit(ctx.logger, "dedup_start", run_id=ctx.run_id, input_count=len(items))
    if not items:
        emit(ctx.logger, "dedup_done", input_count=0, cluster_count=0,
             duplicate_count=0, silent=True)
        return DedupResult(clusters=[], deduped_items=[], input_count=0,
                           cluster_count=0, duplicate_count=0)

    items = _unique_by_link(items)

    priority_of = load_source_priorities(config.sources_registry_path)
    texts = [build_embed_text(it) for it in items]
    try:
        vectors = embedder.embed(texts)
        if len(vectors) != len(items):
            raise ValueError(
                f"embedder returned {len(vectors)} vectors for {len(items)} items "
                "(EmbeddingProvider 1:1 alignment contract violated)")
    except Exception as e:  # noqa: BLE001 - degrade is non-fatal (spec §7)
        emit(ctx.logger, "dedup_embedding_degraded", reason=str(e))
        vectors = [None] * len(items)

    clusters = cluster(items, vectors, priority_of, config, ctx)

    by_emb = {embedding_id(it.link): v for it, v in zip(items, vectors)}
    points: list[tuple] = []
    for c in clusters:
        emit(ctx.logger, "dedup_cluster_created", cluster_id=c.cluster_id, size=c.size)
        vec = by_emb.get(c.primary.embedding_id)
        if vec is not None:
            points.append((c.primary.embedding_id, vec, {"cluster_id": c.cluster_id}))
    store.upsert(points)

    deduped = [c.primary for c in clusters]
    result = DedupResult(clusters=clusters, deduped_items=deduped,
                         input_count=len(items), cluster_count=len(clusters),
                         duplicate_count=len(items) - len(clusters))
    emit(ctx.logger, "dedup_done", input_count=result.input_count,
         cluster_count=result.cluster_count,
         duplicate_count=result.duplicate_count, silent=False)
    return result
