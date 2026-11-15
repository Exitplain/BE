import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import First from "./pages/First";
import Second from "./pages/Second";
import Third from "./pages/Third";
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<First />} />
        <Route path="second" element={<Second />} />
        <Route path="third" element={<Third />} />
      </Route>
    </Routes>
  );
}
