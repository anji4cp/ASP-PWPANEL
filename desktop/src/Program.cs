using System;
using System.Windows.Forms;
namespace AspPwPanelDesktop {
  internal static class Program {
    [STAThread] private static void Main(string[] args) {
      Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
      if (args.Length == 1 && args[0] == "--smoke-test") { using (MainForm form = new MainForm()) form.CreateControl(); return; }
      Application.Run(new MainForm());
    }
  }
}
