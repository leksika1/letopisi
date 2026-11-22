import { NavLink, Route, Routes } from 'react-router-dom';
import { useEditorName } from './lib/editor';
import SearchPage from './pages/SearchPage';
import ReviewPage from './pages/ReviewPage';
import SavedQueriesPage from './pages/SavedQueriesPage';
import DocumentsPage from './pages/DocumentsPage';
import QualityPage from './pages/QualityPage';

const NAV = [
  { to: '/', label: 'Поиск', end: true },
  { to: '/review', label: 'На проверку' },
  { to: '/saved', label: 'Сохранённые запросы' },
  { to: '/documents', label: 'Документы' },
  { to: '/quality', label: 'Качество' },
];

export default function App() {
  const [editor, setEditor] = useEditorName();
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <div>
            <div className="font-serif text-lg font-semibold text-moss-900">Цифровые Летописи природы</div>
            <div className="text-xs text-stone-500">Государственный природный заповедник «Пасвик»</div>
          </div>
          <nav className="flex flex-wrap gap-1">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  `rounded px-3 py-1.5 text-sm ${isActive ? 'bg-moss-100 text-moss-900' : 'text-stone-600 hover:bg-stone-100'}`}
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
          <label className="ml-auto flex items-center gap-2 text-sm text-stone-500">
            Проверяющий:
            <input
              className="input w-48"
              placeholder="Фамилия И.О."
              value={editor}
              onChange={(e) => setEditor(e.target.value)}
            />
          </label>
          {import.meta.env.VITE_MOCK === '1' && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800" title="Данные из contracts/fixtures">
              режим моков
            </span>
          )}
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 py-4">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/observations/:id" element={<SearchPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/saved" element={<SavedQueriesPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/quality" element={<QualityPage />} />
          <Route path="*" element={<p className="text-stone-500">Страница не найдена.</p>} />
        </Routes>
      </main>
    </div>
  );
}
