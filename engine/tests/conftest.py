from __future__ import annotations

import base64
import zipfile
from pathlib import Path

import pytest

CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
ROOT_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
DOCUMENT_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>"""
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def make_docx(tmp_path: Path):
    def build(paragraph_runs: list[list[str]], name: str = "source.docx") -> Path:
        paragraphs = []
        for runs in paragraph_runs:
            body = "".join(
                f'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'
                for text in runs
            )
            paragraphs.append(f"<w:p>{body}</w:p>")
        xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{"".join(paragraphs)}<w:sectPr/></w:body></w:document>""".encode()
        path = tmp_path / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES)
            archive.writestr("_rels/.rels", ROOT_RELS)
            archive.writestr("word/document.xml", xml)
            archive.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS)
            archive.writestr("word/media/image1.png", PNG_1X1)
        return path

    return build


@pytest.fixture
def make_golden_docx(tmp_path: Path):
    def build(name: str = "golden.docx") -> Path:
        content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
  <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
  <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
</Types>"""
        rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/preserved" TargetMode="External"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
  <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
  <Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""
        document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
 <w:body>
  <w:p><w:commentRangeStart w:id="4"/><w:r><w:t>Giữ nguyên</w:t></w:r><w:commentRangeEnd w:id="4"/><w:r><w:commentReference w:id="4"/></w:r></w:p>
  <w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Văn bản sát </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>nhập  nội dung.</w:t></w:r></w:p>
  <w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="2400"/></w:tblGrid><w:tr><w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr><w:p><w:r><w:t>xử lí</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  <w:p><w:hyperlink r:id="rId3"><w:r><w:t>Liên kết ngoài</w:t></w:r></w:hyperlink></w:p>
  <w:p><w:ins w:id="8" w:author="Tester"><w:r><w:t>Tracked text</w:t></w:r></w:ins></w:p>
  <w:sdt><w:sdtPr><w:tag w:val="preserved"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>Content control</w:t></w:r></w:p></w:sdtContent></w:sdt>
  <w:p><w:r><w:drawing><wp:inline><wp:extent cx="9525" cy="9525"/><wp:docPr id="1" name="Picture 1"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rId2"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="9525" cy="9525"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>
  <w:sectPr><w:headerReference w:type="default" r:id="rId4"/><w:footerReference w:type="default" r:id="rId5"/></w:sectPr>
 </w:body>
</w:document>""".encode()
        comments = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:comment w:id="4" w:author="Existing"><w:p><w:r><w:t>Existing comment</w:t></w:r></w:p></w:comment></w:comments>"""
        parts = {
            "[Content_Types].xml": content_types,
            "_rels/.rels": ROOT_RELS,
            "word/document.xml": document,
            "word/_rels/document.xml.rels": rels,
            "word/comments.xml": comments,
            "word/header1.xml": b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Preserved header</w:t></w:r></w:p></w:hdr>',
            "word/footer1.xml": b'<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Preserved footer</w:t></w:r></w:p></w:ftr>',
            "word/media/image1.png": PNG_1X1,
            "customXml/item1.xml": b"<metadata><value>preserved</value></metadata>",
        }
        path = tmp_path / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for part_name, data in parts.items():
                archive.writestr(part_name, data)
        return path

    return build
