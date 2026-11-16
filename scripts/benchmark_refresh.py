"""Read-only refresh benchmark on a disposable copied store; never invokes save or commit."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    allowed = Path(__file__).resolve().parents[1]/'outputs'
    if not args.home.resolve().is_relative_to(allowed.resolve()):
        parser.error('benchmark home must be a disposable copy under this repository outputs')
    if args.source_root:
        sys.path.insert(0,str(args.source_root/'src'))
    from stability_hub.storage import Store
    from stability_hub.workflow import Workflow
    from stability_hub.ledger_review import LedgerReview
    from stability_hub.permissions import actor_for_role, Role
    flow = Workflow(Store(args.home))
    actor = actor_for_role('v5-sandbox','Read-only benchmark',Role.TECHNICIAN)
    with flow.store.connect() as conn:
        upload = conn.execute('SELECT upload_id,count(*) AS n FROM workflow_candidates GROUP BY upload_id ORDER BY n DESC LIMIT 1').fetchone()[0]
    service = LedgerReview(flow)
    start = time.perf_counter()
    first = service.matrix(upload,actor=actor)
    cold = time.perf_counter()-start
    revision = first['preview']['base_revision']
    timings, sizes = [], []
    for _ in range(5):
        start = time.perf_counter()
        response = service.matrix(upload,actor=actor,after_revision=revision)
        body = json.dumps(response,ensure_ascii=False).encode('utf-8')
        timings.append(time.perf_counter()-start)
        sizes.append(len(body))
    start = time.perf_counter()
    service.matrix(upload,actor=actor)
    full = time.perf_counter()-start
    report = {'upload_id':upload,'candidates':len(first['preview']['candidates']), 'cold_seconds':cold,
        'unchanged_seconds':timings,'unchanged_median_seconds':statistics.median(timings),
        'unchanged_max_seconds':max(timings),'response_bytes':sizes,'full_reload_seconds':full}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    main()
