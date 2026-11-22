// Build an optionally instrumented copy of the frontend or merge its browser counters.
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import instrument from 'istanbul-lib-instrument';
import coverage from 'istanbul-lib-coverage';

const frontend = fileURLToPath(new URL('../../../frontend/', import.meta.url));
const [command, target] = process.argv.slice(2);
if (!target || !['build', 'report'].includes(command)) {
  throw new Error('Usage: node measure.mjs build|report <measurement-directory>');
}
const dir = path.resolve(target);
const counters = path.join(dir, 'counters');
if (command === 'build') {
  // An existing directory may contain measurements from a different revision.
  await mkdir(dir);
  await mkdir(counters);
  const { build } = await import(pathToFileURL(createRequire(path.join(frontend, 'package.json')).resolve('vite')).href);
  const baseline = coverage.createCoverageMap({});
  process.env.VITE_MOCK = '0';
  process.chdir(frontend);
  await build({
    root: frontend,
    build: { outDir: path.join(dir, 'dist'), emptyOutDir: false },
    plugins: [{
      name: 'e2e-typescript-coverage',
      enforce: 'pre',
      transform(code, id) {
        const name = path.relative(path.join(frontend, 'src'), id).split(path.sep).join('/');
        if (name.startsWith('../') || path.isAbsolute(name) || name.startsWith('mocks/')) return;
        if (!['.ts', '.tsx'].includes(path.extname(name)) || name.includes('.test.') || name.endsWith('.d.ts')) return;
        const tool = instrument.createInstrumenter({ esModules: true, compact: false,
          parserPlugins: ['jsx', 'typescript'], produceSourceMap: true });
        const transformed = tool.instrumentSync(code, name);
        baseline.addFileCoverage(tool.lastFileCoverage());
        return { code: transformed, map: tool.lastSourceMap() };
      },
    }],
  });
  await writeFile(path.join(dir, 'baseline.json'), JSON.stringify(baseline));
} else {
  const map = coverage.createCoverageMap(JSON.parse(await readFile(path.join(dir, 'baseline.json'), 'utf8')));
  const files = (await readdir(counters)).filter(name => name.endsWith('.json'));
  if (!files.length) throw new Error('No browser counters collected');
  for (const file of files) {
    for (const snapshot of JSON.parse(await readFile(path.join(counters, file), 'utf8'))) {
      map.merge(snapshot);
    }
  }
  const summary = map.getCoverageSummary().toJSON();
  console.log(JSON.stringify({files: map.files().length, ...summary}, null, 2));
  await writeFile(path.join(dir, 'coverage.json'), JSON.stringify(map));
}
