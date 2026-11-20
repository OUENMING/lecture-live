// 跑 web/tests/translate 下全部 .test.ts（node --test glob 在 Node 22 上不认 .ts，这里显式列）。
import { readdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';

const dir = path.resolve(import.meta.dirname);
const files = readdirSync(dir).filter((f) => f.endsWith('.test.ts')).sort().map((f) => path.join(dir, f));
const run = spawnSync(process.execPath, ['--import', 'tsx', '--test', ...files], { stdio: 'inherit' });
process.exit(run.status ?? 1);
