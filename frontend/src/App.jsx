import React from "react";
import { BrowserRouter as Router, Route, Routes } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import BookDetail from "./pages/BookDetail";
import CatalogDashboard from "./pages/CatalogDashboard";
import Library from "./pages/Library";
import NotFound from "./pages/NotFound";
import QA from "./pages/QA";
import Queue from "./pages/Queue";
import Settings from "./pages/Settings";
import VoiceLab from "./pages/VoiceLab";

function App() {
  return (
    <Router>
      <ErrorBoundary>
        <Routes>
          <Route path="/catalog" element={<CatalogDashboard />} />
          <Route path="/" element={<Library />} />
          <Route path="/book/:id" element={<BookDetail />} />
          <Route path="/voice-lab" element={<VoiceLab />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/qa" element={<QA />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </Router>
  );
}

export default App;
