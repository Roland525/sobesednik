"""Synthetic local pipeline measurements; no personal conversation is read."""
import math


def choose_recommendation(rows):
    eligible=[row for row in rows if not row.get('error') and row.get('total')==8
              and row.get('passed',0)>=7 and isinstance(row.get('first_pcm_ms'),(int,float))
              and math.isfinite(row['first_pcm_ms']) and row['first_pcm_ms']>0]
    return min(eligible,key=lambda row:(-row['passed'],row['first_pcm_ms'])) if eligible else None
