#!/usr/bin/env python3
"""Read-only inventory by default. --run requires an exclusive, stopped-app session."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-dir',type=Path,required=True)
    parser.add_argument('--cache-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--profile',choices=['ltx23','ltx25'],default='ltx23')
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--exclusive-offline',action='store_true',help='Operator confirms all other studio/model jobs are stopped')
    parser.add_argument('--app-port',type=int,default=9000)
    parser.add_argument('--size',choices=['704x448','704x1280','1088x1920'],default='704x448')
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--candidate',action='store_true',help='Exact reviewed research source; does not change the app engine')
    parser.add_argument('--memory-mode',choices=['low_ram','resident'],default='low_ram')
    parser.add_argument('--lora-mode',choices=['fused','unfused'],default='fused')
    parser.add_argument('--adapter',type=Path)
    parser.add_argument('--audio',type=Path,help='Explicit offline candidate A2V fixture; no character/lip-sync quality claim')
    parser.add_argument('--temporal-tiles',type=int,choices=range(1,5),default=1)
    parser.add_argument('--spatial-tiles',type=int,choices=range(1,5),default=1)
    args=parser.parse_args()
    output=args.output_dir
    if output.is_symlink() or output.resolve().is_relative_to(args.engine_dir.resolve()) or output.resolve().is_relative_to(args.cache_dir.resolve()):
        parser.error('Output must be a separate local directory outside engine/model caches')
    output.mkdir(parents=True,exist_ok=True)
    # Any app import that needs configuration gets a throwaway environment.
    for prefix in ('OPENFABRIC','REMIQORA'):
        os.environ[prefix+'_CONFIG']=str(output/'config.json')
        os.environ[prefix+'_DATA_DIR']=str(output/'library')
    os.environ['OPENFABRIC_MODULE_ROOT']=str(output/'modules')
    os.environ['SEED_VC_DIR']=str(output/'seed')
    from app.video_benchmark import inventory, run_case
    from app.video_candidate import CandidateSettings
    from app.video_engine import VideoEngineError, ProfileId
    profile: ProfileId='ltx23' if args.profile=='ltx23' else 'ltx25'
    if not args.candidate and (args.memory_mode != 'low_ram' or args.lora_mode != 'fused' or args.adapter is not None or args.audio is not None or args.temporal_tiles != 1 or args.spatial_tiles != 1):
        parser.error('Memory/adapter experiments require --candidate')
    report=inventory(args.engine_dir,args.cache_dir,profile,candidate=args.candidate)
    if args.run:
        width,height=(int(value) for value in args.size.split('x'))
        candidate = CandidateSettings(memory_mode='resident' if args.memory_mode == 'resident' else 'low_ram',
            lora_mode='unfused' if args.lora_mode == 'unfused' else 'fused', width=width, height=height,
            reference=args.reference, adapter=args.adapter, source_audio=args.audio,
            temporal_tiles=args.temporal_tiles, spatial_tiles=args.spatial_tiles) if args.candidate else None
        try:
            report=asyncio.run(run_case(report,args.engine_dir,args.cache_dir,output,exclusive=args.exclusive_offline,
                app_port=args.app_port,width=width,height=height,reference=args.reference,candidate_settings=candidate))
        except (ValueError,VideoEngineError) as exc:
            report.inference_skip_reason=exc.code if isinstance(exc,VideoEngineError) else str(exc)
    payload=report.model_dump_json(indent=2)
    with tempfile.NamedTemporaryFile('w',dir=output,prefix='.report-',delete=False,encoding='utf-8') as handle:
        temporary=Path(handle.name);handle.write(payload+'\n');handle.flush();os.fsync(handle.fileno())
    temporary.replace(output/'report.json')
    print(json.dumps({'report':str(output/'report.json'),'inference_executed':report.inference_executed,
        'skip_reason':report.inference_skip_reason,'error_code':report.error_code}))
    return 1 if report.error_code else 0


if __name__=='__main__':
    raise SystemExit(main())
