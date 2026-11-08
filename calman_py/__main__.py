import argparse
from pathlib import Path
from .color_math import TargetConfig
from .patterns import PATTERNS
from .devices import SimulatedRig
from .session import Session,run_measurements


def main():
    parser=argparse.ArgumentParser(description='ColorLab SDR 测量工作台')
    parser.add_argument('--simulate',action='store_true',help='无界面执行完整模拟测量')
    parser.add_argument('--output',type=Path,default=Path('output/demo-session.json'))
    parser.add_argument('--profile',choices=['reference','uncalibrated','wide_unclamped','warm_aged'],default='uncalibrated')
    args=parser.parse_args()
    if args.simulate:
        rig=SimulatedRig(args.profile); rig.connect()
        session=Session(TargetConfig(),True,list(PATTERNS),{'profile':args.profile,'settle_seconds':0,'window_percent':100},save_path=args.output)
        try: run_measurements(session,rig,rig,settle=0)
        finally: rig.disconnect()
        session.export_csv(args.output.with_suffix('.csv'))
        import json
        print(json.dumps(session.summary(),ensure_ascii=False,indent=2))
        print(f'SIMULATED · {args.output.resolve()}')
    else:
        from .ui import launch
        raise SystemExit(launch())


if __name__=='__main__': main()
