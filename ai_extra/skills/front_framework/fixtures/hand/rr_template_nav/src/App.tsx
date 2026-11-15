import { Routes, Route, Link, useNavigate } from "react-router-dom";
function Start() {
  const navigate = useNavigate();
  const id = Math.random();
  return <div><button onClick={() => navigate(`/w/${id}`)}>dyn</button><Link to="/finish">go</Link></div>;
}
function Finish() { return <p>done</p>; }
export default function App() {
  return <Routes><Route path="/" element={<Start />} /><Route path="/finish" element={<Finish />} /></Routes>;
}
