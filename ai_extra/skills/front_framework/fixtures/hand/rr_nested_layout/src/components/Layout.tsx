import { Link, Outlet } from "react-router-dom";
export default function Layout() {
  return <div><nav><Link to="/third">jump</Link><Link to="/second">two</Link></nav><Outlet /></div>;
}
