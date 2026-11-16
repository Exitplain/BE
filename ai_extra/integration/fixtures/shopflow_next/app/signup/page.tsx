import Link from "next/link";
export default function Page() {
  return <form><input type="email" /><input type="password" /><Link href="/checkout">next</Link></form>;
}
