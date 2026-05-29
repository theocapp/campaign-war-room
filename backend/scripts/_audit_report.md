# Phase 0.5 preflight audit report
_Generated 2026-05-29T02:56:50.147205Z_
- ✅ PASS: 169
- ⚠️  WARN: 15
- ❌ FAIL: 6

## FAILs — block Phase 1 until resolved

- **json** — source_items.relevance_reasons: 187 rows fail JSON parse
  ```json
  {
  "table": "source_items",
  "column": "relevance_reasons",
  "rows": [
    {
      "id": 17751,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "The article highlights an ongoing debate relevant to law enforcement and communi"
    },
    {
      "id": 17753,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "The article touches on policy themes in Scranton/Wilkes-Barre, PA-08, but doesn'"
    },
    {
      "id": 17782,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "This article presents a positive narrative about Bresnahan's work, which may be "
    },
    {
      "id": 18044,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "While this article does not directly involve Paige Cognetti or Rob Bresnahan, it"
    },
    {
      "id": 18107,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "This article discusses a sales tax increase to fund healthcare services, which i"
    },
    {
      "id": 18161,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "Cognetti's recent campaign activity may indicate growing momentum in the race"
    },
    {
      "id": 18211,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "Cognetti has officially entered the race and delivered a high-impact kickoff mes"
    },
    {
      "id": 18218,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "A fact sheet from Families USA highlights the impacts of deep cuts to Medicaid, "
    },
    {
      "id": 18387,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "This article highlights Rep. Bresnahan's potential involvement in questionable f"
    },
    {
      "id": 18388,
      "error": "Expecting value: line 1 column 1 (char 0)",
      "sample": "Biden's connection to Scranton may not directly affect the campaign, but it coul"
    }
  ],
  "total_bad": 187
}
  ```
- **fk** — frame_cluster_matches.story_cluster_id → story_clusters.id: 53 orphan(s)
  ```json
  {
  "child_table": "frame_cluster_matches",
  "child_column": "story_cluster_id",
  "parent_table": "story_clusters",
  "orphan_count": 53,
  "sample_child_ids": [
    2075,
    1869,
    1868,
    782,
    1101,
    1102,
    1786,
    832,
    1789,
    2062
  ]
}
  ```
- **fk** — cluster_opponent_activities.story_cluster_id → story_clusters.id: 72 orphan(s)
  ```json
  {
  "child_table": "cluster_opponent_activities",
  "child_column": "story_cluster_id",
  "parent_table": "story_clusters",
  "orphan_count": 72,
  "sample_child_ids": [
    302,
    303,
    304,
    354,
    121,
    122,
    123,
    124,
    125,
    126
  ]
}
  ```
- **fk** — entity_mentions.entity_id → entities.id: 1 orphan(s)
  ```json
  {
  "child_table": "entity_mentions",
  "child_column": "entity_id",
  "parent_table": "entities",
  "orphan_count": 1,
  "sample_child_ids": [
    10037
  ]
}
  ```
- **fk** — claims.subject_id → entities.id: 25 orphan(s)
  ```json
  {
  "child_table": "claims",
  "child_column": "subject_id",
  "parent_table": "entities",
  "orphan_count": 25,
  "sample_child_ids": [
    200,
    285,
    1413,
    498,
    562,
    802,
    1305,
    1306,
    1307,
    1308
  ]
}
  ```
- **fk** — claims.object_id → entities.id: 26 orphan(s)
  ```json
  {
  "child_table": "claims",
  "child_column": "object_id",
  "parent_table": "entities",
  "orphan_count": 26,
  "sample_child_ids": [
    6,
    112,
    633,
    930,
    10,
    72,
    1792,
    1820,
    35,
    367
  ]
}
  ```

## WARNs — review, then proceed with eyes open

- **enum** — source_items.race_relevance_label: 1 undocumented value(s)
  ```json
  {
  "table": "source_items",
  "column": "race_relevance_label",
  "allowed": [
    "irrelevant",
    "low",
    "medium",
    "high",
    "<NULL>"
  ],
  "found": [
    {
      "value": "critical",
      "count": 660
    }
  ]
}
  ```
- **enum** — source_items.source_owner_type: 5 undocumented value(s)
  ```json
  {
  "table": "source_items",
  "column": "source_owner_type",
  "allowed": [
    "unclear",
    "candidate",
    "opponent",
    "media",
    "<NULL>"
  ],
  "found": [
    {
      "value": "candidate_statement",
      "count": 1
    },
    {
      "value": "community/manual",
      "count": 1261
    },
    {
      "value": "opponent_statement",
      "count": 100
    },
    {
      "value": "outside_group_statement",
      "count": 36
    },
    {
      "value": "party_committee_statement",
      "count": 1392
    }
  ]
}
  ```
- **enum** — source_items.actionability_label: 3 undocumented value(s)
  ```json
  {
  "table": "source_items",
  "column": "actionability_label",
  "allowed": [
    "ignore",
    "low",
    "medium",
    "high",
    "<NULL>"
  ],
  "found": [
    {
      "value": "monitor",
      "count": 4089
    },
    {
      "value": "respond",
      "count": 752
    },
    {
      "value": "review",
      "count": 1053
    }
  ]
}
  ```
- **enum** — source_items.extraction_quality_label: 1 undocumented value(s)
  ```json
  {
  "table": "source_items",
  "column": "extraction_quality_label",
  "allowed": [
    "good",
    "medium",
    "poor",
    "<NULL>"
  ],
  "found": [
    {
      "value": "mixed",
      "count": 193
    }
  ]
}
  ```
- **enum** — source_items.content_category: 7 undocumented value(s)
  ```json
  {
  "table": "source_items",
  "column": "content_category",
  "allowed": [
    "irrelevant",
    "candidate_news",
    "opponent_news",
    "race_news",
    "policy",
    "election_admin",
    "endorsement",
    "other",
    "<NULL>"
  ],
  "found": [
    {
      "value": "campaign",
      "count": 1928
    },
    {
      "value": "entertainment",
      "count": 388
    },
    {
      "value": "food",
      "count": 288
    },
    {
      "value": "generic_crime",
      "count": 343
    },
    {
      "value": "priority_issue",
      "count": 129
    },
    {
      "value": "sports",
      "count": 850
    },
    {
      "value": "weather",
      "count": 223
    }
  ]
}
  ```
- **enum** — narrative_frames.momentum_signal: 2 undocumented value(s)
  ```json
  {
  "table": "narrative_frames",
  "column": "momentum_signal",
  "allowed": [
    "viral",
    "missing_coverage",
    "elite_only",
    "stable",
    "<NULL>"
  ],
  "found": [
    {
      "value": "amplified",
      "count": 4
    },
    {
      "value": "no_trend_signal",
      "count": 1
    }
  ]
}
  ```
- **enum** — narrative_frame_mentions.matched_by: 1 undocumented value(s)
  ```json
  {
  "table": "narrative_frame_mentions",
  "column": "matched_by",
  "allowed": [
    "llm",
    "human"
  ],
  "found": [
    {
      "value": "promoted_from_candidate",
      "count": 103
    }
  ]
}
  ```
- **enum** — frame_cluster_matches.matched_by: 1 undocumented value(s)
  ```json
  {
  "table": "frame_cluster_matches",
  "column": "matched_by",
  "allowed": [
    "llm",
    "human"
  ],
  "found": [
    {
      "value": "promoted_from_candidate",
      "count": 148
    }
  ]
}
  ```
- **enum** — frame_cluster_matches.source_type: 1 undocumented value(s)
  ```json
  {
  "table": "frame_cluster_matches",
  "column": "source_type",
  "allowed": [
    "cluster_runtime",
    "cluster_backfill",
    "cluster_retrigger"
  ],
  "found": [
    {
      "value": "promoted_from_candidate",
      "count": 147
    }
  ]
}
  ```
- **enum** — entity_mentions.extraction_method: 3 undocumented value(s)
  ```json
  {
  "table": "entity_mentions",
  "column": "extraction_method",
  "allowed": [
    "seed",
    "alias",
    "embedding",
    "llm"
  ],
  "found": [
    {
      "value": "fresh",
      "count": 4236
    },
    {
      "value": "seed_alias",
      "count": 789
    },
    {
      "value": "seed_name",
      "count": 14716
    }
  ]
}
  ```
- **enum** — source_monitors.monitor_type: 3 undocumented value(s)
  ```json
  {
  "table": "source_monitors",
  "column": "monitor_type",
  "allowed": [
    "rss",
    "search_query",
    "manual",
    "webpage"
  ],
  "found": [
    {
      "value": "fec_ie_district",
      "count": 1
    },
    {
      "value": "twitter_profile",
      "count": 3
    },
    {
      "value": "youtube",
      "count": 2
    }
  ]
}
  ```
- **utf8** — source_items.raw_text: 20 row(s) contain U+FFFD (likely encoding error during ingestion)
  ```json
  {
  "table": "source_items",
  "column": "raw_text",
  "samples": [
    {
      "id": 55,
      "sample": "%PDF-1.6\r\n%\ufffd\ufffd\ufffd\ufffd\r\n1 0 obj\r\n<</Metadata 2 0 R /OCProperties<</D<</ON[ 14 0 R  15 0"
    },
    {
      "id": 71,
      "sample": "%PDF-1.4\r%\ufffd\ufffd\ufffd\ufffd\r\n554 0 obj\r<</Linearized 1/L 193638/O 557/E 71027/N 5/T 182442/H "
    },
    {
      "id": 130,
      "sample": "%PDF-1.5\n%\ufffd\ufffd\ufffd\ufffd\n4 0 obj\n<</Filter/DCTDecode/ColorSpace/DeviceRGB/Type/XObject/Sub"
    },
    {
      "id": 134,
      "sample": "%PDF-1.7\r%\ufffd\ufffd\ufffd\ufffd\r\n2091 0 obj\r<</Filter/FlateDecode/First 7/Length 165/N 1/Type/Obj"
    },
    {
      "id": 140,
      "sample": "%PDF-1.7\r%\ufffd\ufffd\ufffd\ufffd\r\n2091 0 obj\r<</Filter/FlateDecode/First 7/Length 165/N 1/Type/Obj"
    },
    {
      "id": 11242,
      "sample": "Vice President JD Vance is hitting his home state on Monday to continue promotin"
    },
    {
      "id": 11728,
      "sample": "Vice President\ufffdJD Vance\ufffdon Wednesday will head to the swing political turf of no"
    },
    {
      "id": 13839,
      "sample": "%PDF-1.7\r%\ufffd\ufffd\ufffd\ufffd\r\n2367 0 obj\r<</Linearized 1/L 597084/O 2369/E 79392/N 20/T 596430"
    },
    {
      "id": 13840,
      "sample": "%PDF-1.6\r%\ufffd\ufffd\ufffd\ufffd\r\n937 0 obj\r<</Linearized 1/L 4523749/O 939/E 752978/N 21/T 452301"
    },
    {
      "id": 13842,
      "sample": "%PDF-1.5\r\n%\ufffd\ufffd\ufffd\ufffd\r\n1 0 obj\r\n<</Type/Catalog/Pages 2 0 R/Lang(en-US) /StructTreeRoo"
    }
  ]
}
  ```
- **utf8** — source_items.summary: 5 row(s) contain U+FFFD (likely encoding error during ingestion)
  ```json
  {
  "table": "source_items",
  "column": "summary",
  "samples": [
    {
      "id": 55,
      "sample": "%PDF-1.6 %\ufffd\ufffd\ufffd\ufffd 1 0 obj <</Metadata 2 0 R /OCProperties<</D<</ON[ 14 0 R 15 0 R 1"
    },
    {
      "id": 71,
      "sample": "%PDF-1.4 %\ufffd\ufffd\ufffd\ufffd 554 0 obj <</Linearized 1/L 193638/O 557/E 71027/N 5/T 182442/H ["
    },
    {
      "id": 130,
      "sample": "%PDF-1.5\n%\ufffd\ufffd\ufffd\ufffd\n4 0 obj\n<</Filter/DCTDecode/ColorSpace/DeviceRGB/Type/XObject/Sub"
    },
    {
      "id": 134,
      "sample": "%PDF-1.7 %\ufffd\ufffd\ufffd\ufffd 2091 0 obj <</Filter/FlateDecode/First 7/Length 165/N 1/Type/ObjS"
    },
    {
      "id": 140,
      "sample": "%PDF-1.7 %\ufffd\ufffd\ufffd\ufffd 2091 0 obj <</Filter/FlateDecode/First 7/Length 165/N 1/Type/ObjS"
    }
  ]
}
  ```
- **nul_byte** — source_items.raw_text: 47 row(s) contain embedded NUL (migration strips silently)
  ```json
  {
  "table": "source_items",
  "column": "raw_text",
  "row_count": 47,
  "sample_ids": [
    71,
    130,
    134,
    140,
    13839,
    13840,
    13842,
    13898,
    13904,
    13919,
    13923,
    13924,
    13934,
    13935,
    14213,
    14215,
    14277,
    14775,
    14793,
    15208
  ]
}
  ```
- **nul_byte** — source_items.summary: 3 row(s) contain embedded NUL (migration strips silently)
  ```json
  {
  "table": "source_items",
  "column": "summary",
  "row_count": 3,
  "sample_ids": [
    130,
    134,
    140
  ]
}
  ```
