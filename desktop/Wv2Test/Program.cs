using System;
using System.Windows.Forms;

namespace Wv2Test;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (args.Length > 0 && args[0] == "--plain")
        {
            Application.Run(new PlainForm());
        }
        else
        {
            Application.Run(new TestForm());
        }
    }
}

public class PlainForm : Form
{
    public PlainForm()
    {
        Text = "PlainKeyTest";
        FormBorderStyle = FormBorderStyle.None;
        BackColor = Color.Lime;
        TransparencyKey = Color.Lime;
        TopMost = true;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Location = new Point(100, 100);
        Size = new Size(460, 400);
    }
}
