import React from "react";
import { BrowserRouter as Router, Route, Routes } from "react-router-dom";
import ErrorBoundary from "./components/ErrorBoundary";
import BookDetail from "./pages/BookDetail";
import Library from "./pages/Library";
import QA from "./pages/QA";
import Queue from "./pages/Queue";
import Settings from "./pages/Settings";
import VoiceLab from "./pages/VoiceLab";

function App() {
  return (
    <Router>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/book/:id" element={<BookDetail />} />
          <Route path="/voice-lab" element={<VoiceLab />} />
          <Route path="/queue" element={<Queue />} />
          <Route path="/qa" element={<QA />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </ErrorBoundary>
    </Router>
  );
}

export default App;
