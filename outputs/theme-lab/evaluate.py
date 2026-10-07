"""Evaluate independently labelled examples; no model self-confidence probabilities."""
import json
import sys

def evaluate(rows):
    if not rows:
        raise ValueError('Evaluation requires a labelled reference set')
    answered = [r for r in rows if r.get('predicted') is not None]
    labels = sorted({r['actual'] for r in rows} | {r['predicted'] for r in answered})
    metrics = {}
    for label in labels:
        tp = sum(r['actual'] == label and r.get('predicted') == label for r in rows)
        fp = sum(r['actual'] != label and r.get('predicted') == label for r in rows)
        fn = sum(r['actual'] == label and r.get('predicted') != label for r in rows)
        precision, recall = tp/(tp+fp) if tp+fp else 0, tp/(tp+fn) if tp+fn else 0
        metrics[label] = {'precision':precision, 'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0}
    bands = {}
    for band in ('low','medium','high'):
        group = [r for r in answered if r.get('confidence_band') == band]
        bands[band] = {'count':len(group),'observed_accuracy':sum(r['actual']==r['predicted'] for r in group)/len(group) if group else None}
    errors = [abs(r['point']-r['reference_point'])*100 for r in rows if r.get('point') is not None and r.get('reference_point') is not None]
    ranges = [r for r in rows if all(r.get(k) is not None for k in ('lower','upper','reference_lower','reference_upper'))]
    return {'examples':len(rows),'abstention_rate':1-len(answered)/len(rows),
            'answered_accuracy':sum(r['actual']==r['predicted'] for r in answered)/len(answered) if answered else None,
            'classification':metrics, 'rubric_bands':bands,
            'exposure_mean_absolute_error_pp':sum(errors)/len(errors) if errors else None,
            'reference_range_overlap_rate':sum(max(r['lower'],r['reference_lower'])<=min(r['upper'],r['reference_upper']) for r in ranges)/len(ranges) if ranges else None,
            'note':'Rubric bands are non-probabilistic. Use independently reviewed held-out examples; these metrics do not establish calibration.'}

if __name__ == '__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python evaluate.py labelled_examples.json')
    with open(sys.argv[1],encoding='utf-8') as f: rows=json.load(f)
    print(json.dumps(evaluate(rows),indent=2))
