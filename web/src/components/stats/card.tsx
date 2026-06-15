import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export function StatCard({
  title,
  footer,
  children,
}: {
  title: string;
  footer: string;
  children?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <p>{title}</p>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-3xl font-extrabold">{children}</p>
      </CardContent>
      <CardFooter>
        <CardDescription>
          <p>{footer}</p>
        </CardDescription>
      </CardFooter>
    </Card>
  );
}
