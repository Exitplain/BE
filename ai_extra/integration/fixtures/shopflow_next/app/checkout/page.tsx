import Link from "next/link";
export default function Page() {
  return <form><input name="a" /><input name="b" /><input name="c" /><input name="d" /><Link href="/done">pay</Link></form>;
}
