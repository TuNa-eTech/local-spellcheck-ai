using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;

if (args.Length == 0)
{
    Console.Error.WriteLine("Usage: OpenXmlValidator <document.docx> [...]");
    return 2;
}

var validator = new OpenXmlValidator(FileFormatVersions.Office2016);
var failed = false;
foreach (var path in args)
{
    try
    {
        using var document = WordprocessingDocument.Open(path, false);
        var errors = validator.Validate(document).Take(100).ToList();
        if (errors.Count == 0)
        {
            Console.WriteLine($"VALID {path}");
            continue;
        }
        failed = true;
        Console.Error.WriteLine($"INVALID {path}: {errors.Count} validation error(s)");
        foreach (var error in errors)
        {
            Console.Error.WriteLine($"- {error.Description} [{error.Path?.XPath}]");
        }
    }
    catch (Exception error)
    {
        failed = true;
        Console.Error.WriteLine($"UNREADABLE {path}: {error.Message}");
    }
}

return failed ? 1 : 0;
