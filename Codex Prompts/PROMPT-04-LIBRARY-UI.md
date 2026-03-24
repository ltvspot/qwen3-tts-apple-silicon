# PROMPT-04: Library Home Page & Book Card Component

**Objective:** Create the Library home page with book grid, search, filtering, and sorting. Implement a reusable BookCard component.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Library Home Page

**File:** `frontend/src/pages/Library.jsx`

Create the main library page with search, filtering, sorting, and a grid of books.

```jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import BookCard from '../components/BookCard';

export default function Library() {
  const navigate = useNavigate();
  const [books, setBooks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sortBy, setSortBy] = useState('id');
  const [stats, setStats] = useState(null);

  // ========================================================================
  // Data Fetching
  // ========================================================================

  useEffect(() => {
    fetchLibrary();
  }, [statusFilter, sortBy]);

  const fetchLibrary = async () => {
    try {
      setLoading(true);
      const params = new URLSearchParams();
      if (statusFilter) params.append('status_filter', statusFilter);

      const response = await fetch(`/api/library?${params}`);
      if (!response.ok) throw new Error('Failed to fetch library');

      const data = await response.json();
      setBooks(data.books);
      setStats(data.stats);
    } catch (error) {
      console.error('Error fetching library:', error);
    } finally {
      setLoading(false);
    }
  };

  // ========================================================================
  // Search & Filter Logic
  // ========================================================================

  const filteredBooks = books
    .filter(book => {
      const search = searchTerm.toLowerCase();
      return (
        book.title.toLowerCase().includes(search) ||
        book.author.toLowerCase().includes(search)
      );
    })
    .sort((a, b) => {
      switch (sortBy) {
        case 'title':
          return a.title.localeCompare(b.title);
        case 'author':
          return a.author.localeCompare(b.author);
        case 'page_count':
          return (b.page_count || 0) - (a.page_count || 0);
        case 'id':
        default:
          return a.id - b.id;
      }
    });

  // ========================================================================
  // Handlers
  // ========================================================================

  const handleCardClick = (bookId) => {
    navigate(`/book/${bookId}`);
  };

  const handleRefreshLibrary = async () => {
    try {
      setLoading(true);
      const response = await fetch('/api/library/scan', { method: 'POST' });
      if (response.ok) {
        await fetchLibrary();
      }
    } catch (error) {
      console.error('Error scanning library:', error);
    } finally {
      setLoading(false);
    }
  };

  // ========================================================================
  // Render
  // ========================================================================

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 to-slate-800">
      {/* Header */}
      <header className="border-b border-slate-700 bg-slate-900 sticky top-0 z-50 shadow-lg">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-4xl font-bold text-white">Alexandria</h1>
              <p className="text-slate-400 text-sm mt-1">Audiobook Narrator</p>
            </div>
            <button
              onClick={handleRefreshLibrary}
              disabled={loading}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium disabled:opacity-50 transition"
            >
              {loading ? 'Scanning...' : 'Scan Library'}
            </button>
          </div>

          {/* Stats Bar */}
          {stats && (
            <div className="grid grid-cols-7 gap-2 text-sm mb-6">
              <div className="bg-slate-700 rounded-lg p-3">
                <div className="text-slate-400 text-xs font-semibold uppercase">Total</div>
                <div className="text-white text-xl font-bold">{stats.not_started + stats.parsed + stats.generating + stats.generated + stats.qa + stats.qa_approved + stats.exported}</div>
              </div>
              <div className="bg-gray-600 rounded-lg p-3">
                <div className="text-slate-300 text-xs font-semibold uppercase">Not Started</div>
                <div className="text-white text-xl font-bold">{stats.not_started}</div>
              </div>
              <div className="bg-blue-700 rounded-lg p-3">
                <div className="text-blue-200 text-xs font-semibold uppercase">Parsed</div>
                <div className="text-white text-xl font-bold">{stats.parsed}</div>
              </div>
              <div className="bg-amber-700 rounded-lg p-3">
                <div className="text-amber-200 text-xs font-semibold uppercase">Generating</div>
                <div className="text-white text-xl font-bold">{stats.generating}</div>
              </div>
              <div className="bg-purple-700 rounded-lg p-3">
                <div className="text-purple-200 text-xs font-semibold uppercase">QA</div>
                <div className="text-white text-xl font-bold">{stats.qa}</div>
              </div>
              <div className="bg-green-700 rounded-lg p-3">
                <div className="text-green-200 text-xs font-semibold uppercase">Complete</div>
                <div className="text-white text-xl font-bold">{stats.generated}</div>
              </div>
              <div className="bg-yellow-700 rounded-lg p-3">
                <div className="text-yellow-200 text-xs font-semibold uppercase">Exported</div>
                <div className="text-white text-xl font-bold">{stats.exported}</div>
              </div>
            </div>
          )}

          {/* Search & Filter Controls */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Search Bar */}
            <div className="md:col-span-2">
              <input
                type="text"
                placeholder="Search by title or author..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="w-full px-4 py-3 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            {/* Status Filter Dropdown */}
            <div>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="w-full px-4 py-3 bg-slate-800 border border-slate-600 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              >
                <option value="">All Statuses</option>
                <option value="not_started">Not Started</option>
                <option value="parsed">Parsed</option>
                <option value="generating">Generating</option>
                <option value="generated">Generated</option>
                <option value="qa">QA Review</option>
                <option value="qa_approved">QA Approved</option>
                <option value="exported">Exported</option>
              </select>
            </div>
          </div>

          {/* Sort Controls */}
          <div className="mt-4 flex gap-2 flex-wrap">
            <span className="text-slate-400 text-sm font-medium flex items-center">Sort By:</span>
            {['id', 'title', 'author', 'page_count'].map(option => (
              <button
                key={option}
                onClick={() => setSortBy(option)}
                className={`px-3 py-1 rounded-full text-sm font-medium transition ${
                  sortBy === option
                    ? 'bg-blue-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {option === 'id' ? 'ID' : option.charAt(0).toUpperCase() + option.slice(1).replace('_', ' ')}
              </button>
            ))}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {loading && !books.length ? (
          <div className="flex items-center justify-center h-64">
            <div className="text-slate-400 text-lg">Loading library...</div>
          </div>
        ) : filteredBooks.length === 0 ? (
          <div className="flex items-center justify-center h-64">
            <div className="text-slate-400 text-lg">
              {books.length === 0 ? 'No books in library. Click "Scan Library" to index manuscripts.' : 'No books match your search.'}
            </div>
          </div>
        ) : (
          <>
            <div className="mb-4 text-slate-400 text-sm">
              Showing {filteredBooks.length} of {books.length} books
            </div>

            {/* Book Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
              {filteredBooks.map(book => (
                <BookCard
                  key={book.id}
                  book={book}
                  onClick={() => handleCardClick(book.id)}
                />
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
```

---

### 2. BookCard Component

**File:** `frontend/src/components/BookCard.jsx`

Create a reusable book card component displayed in the grid.

```jsx
import React from 'react';

const STATUS_COLORS = {
  'not_started': { bg: 'bg-gray-600', text: 'text-gray-100', label: 'Not Started' },
  'parsed': { bg: 'bg-blue-600', text: 'text-blue-100', label: 'Parsed' },
  'generating': { bg: 'bg-amber-600', text: 'text-amber-100', label: 'Generating' },
  'generated': { bg: 'bg-green-600', text: 'text-green-100', label: 'Generated' },
  'qa': { bg: 'bg-purple-600', text: 'text-purple-100', label: 'QA Review' },
  'qa_approved': { bg: 'bg-emerald-600', text: 'text-emerald-100', label: 'QA Approved' },
  'exported': { bg: 'bg-yellow-600', text: 'text-yellow-100', label: 'Exported' },
};

export default function BookCard({ book, onClick }) {
  const statusColor = STATUS_COLORS[book.status] || STATUS_COLORS['not_started'];

  return (
    <div
      onClick={onClick}
      className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden hover:border-blue-500 hover:shadow-lg hover:shadow-blue-500/20 transition-all cursor-pointer group"
    >
      {/* Book Cover Placeholder */}
      <div className="bg-gradient-to-br from-blue-600 to-purple-700 h-48 flex items-center justify-center relative overflow-hidden">
        <div className="absolute inset-0 opacity-20 group-hover:opacity-30 transition">
          <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCBmaWxsPSJub25lIiBzdHJva2U9IiNmZmYiIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCIvPjwvc3ZnPg==')] bg-repeat"></div>
        </div>
        <div className="text-center relative z-10">
          <div className="text-white text-sm font-bold opacity-75">ID: {book.id}</div>
        </div>
      </div>

      {/* Card Body */}
      <div className="p-4">
        {/* Status Badge */}
        <div className="mb-3">
          <span className={`inline-block px-3 py-1 rounded-full text-xs font-bold ${statusColor.bg} ${statusColor.text}`}>
            {statusColor.label}
          </span>
        </div>

        {/* Title */}
        <h3 className="text-lg font-bold text-white mb-1 line-clamp-2 group-hover:text-blue-400 transition">
          {book.title}
        </h3>

        {/* Subtitle (if present) */}
        {book.subtitle && (
          <p className="text-sm text-slate-400 mb-2 line-clamp-1">
            {book.subtitle}
          </p>
        )}

        {/* Author */}
        <p className="text-sm text-slate-400 mb-3">
          by <span className="text-slate-300 font-medium">{book.author}</span>
        </p>

        {/* Metadata Footer */}
        <div className="flex items-center justify-between text-xs text-slate-400 border-t border-slate-700 pt-3">
          <div className="flex gap-3">
            {book.page_count && (
              <div>
                <span className="font-semibold">{book.page_count}</span>
                <span className="text-slate-500 ml-1">pages</span>
              </div>
            )}
            {book.chapter_count && (
              <div>
                <span className="font-semibold">{book.chapter_count}</span>
                <span className="text-slate-500 ml-1">chapters</span>
              </div>
            )}
          </div>
          {book.trim_size && (
            <div className="text-slate-500">{book.trim_size}</div>
          )}
        </div>
      </div>
    </div>
  );
}
```

---

## Styling Notes

**Color Scheme for Status Badges:**
- `not_started`: Gray (`bg-gray-600`)
- `parsed`: Blue (`bg-blue-600`)
- `generating`: Amber (`bg-amber-600`)
- `generated`: Green (`bg-green-600`)
- `qa`: Purple (`bg-purple-600`)
- `qa_approved`: Emerald (`bg-emerald-600`)
- `exported`: Yellow (`bg-yellow-600`)

**Layout Details:**
- **Header:** Sticky position, dark gradient background
- **Stats Bar:** 7 columns showing counts per status (responsive, collapses on mobile)
- **Search/Filter:** Full width on mobile, 3-column layout on desktop
- **Book Grid:** 1 column (mobile), 2 (tablet), 3 (desktop), 4 (4K)
- **BookCard:** Gradient cover, status badge, title/author/metadata footer
- **Hover Effects:** Border glow (blue), text color change, shadow enhancement

---

## Acceptance Criteria

1. **Library Page Loads:**
   - Page renders at `/` without errors
   - Fetches data from `GET /api/library` on mount
   - Displays grid of books with BookCard components

2. **Search Functionality:**
   - Typing in search bar filters books by title or author instantly
   - Search is case-insensitive
   - Shows updated count as user types

3. **Status Filter:**
   - Dropdown allows filtering by status
   - "All Statuses" shows all books
   - Single status filters correctly
   - Refetches from API when filter changes

4. **Sorting:**
   - Buttons allow sorting by: ID, Title, Author, Page Count
   - Current sort is highlighted
   - Sorting updates grid order without API fetch (client-side)

5. **Stats Bar:**
   - Displays count for each status
   - Updates when library is scanned
   - Shows total across all statuses

6. **BookCard Component:**
   - Displays book ID, title, subtitle, author
   - Shows page count and chapter count
   - Displays trim size
   - Status badge with correct color
   - Hover effects (glow, text change)
   - Click navigates to `/book/{id}`

7. **Scan Library Button:**
   - Calls `POST /api/library/scan`
   - Shows "Scanning..." while loading
   - Refreshes data on completion
   - Disabled while loading

8. **Responsive Design:**
   - Works on mobile (1 column grid)
   - Works on tablet (2 columns, responsive controls)
   - Works on desktop (3-4 columns)
   - No horizontal scroll

9. **Empty State:**
   - Shows message when no books in library
   - Shows message when search returns no results

10. **Git Commit:**
    - All changes committed with message: `[PROMPT-04] Library home page and book card component`

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **Tailwind CSS:** https://tailwindcss.com/
- **React Router:** https://reactrouter.com/
