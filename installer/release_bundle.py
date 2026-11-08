"""The small end-user ZIP contains one installer and two complete notice files."""


def files(exe, kit, ssh_license, ssh_notice):
    intro = (
        'Steam Frame PCVR for VRChat — License / 利用許諾\n\n'
        '独自部分はMITライセンスです。組み込み・改変・再配布・商用利用ができます。\n'
        '元作者の名前を含む著作権表示と、ライセンス全文を残してください。\n'
        '本プログラムは現状有姿・無保証です。外部部品には各元ライセンスが適用されます。\n'
        '外部部品の条件はTHIRD_PARTY_LICENSES.txt、独自部分の条件は以下のMIT本文を確認してください。\n\n'
        'Original components are MIT licensed. Preserve the original authors\' copyright notices\n'
        'and the full license text when distributing copies or substantial portions.\n'
        'Third-party components retain their own licenses; see THIRD_PARTY_LICENSES.txt.\n'
        'The MIT text below defines the terms. The software is provided as is without warranty.\n\n'
    ).encode()
    header = (
        'Steam Frame PCVR for VRChat — Third-party licenses / 外部部品のライセンス\n\n'
        '各ライセンス原文を以下に収録しています。第三者部品を一括してMITへ変更するものではありません。\n'
        '修正を含む対応ソース・ビルド手順・説明書はEXE内にあります。\n'
        'アプリの「利用許諾・ソース」からZIPを取り出せます。インストール前にも開けます。\n'
        'Corresponding source and build instructions are embedded in FrameSetup.exe.\n'
        'Open the license/source button before or after installation to extract them.\n\n'
    ).encode()
    notices = [('THIRD_PARTY.md', kit['THIRD_PARTY.md'])]
    notices += [(name, data) for name, data in sorted(kit.items()) if name.startswith('licenses/')]
    notices += [('OpenSSH-LICENSE.txt', ssh_license), ('OpenSSH-NOTICE.txt', ssh_notice)]
    combined = header
    for name, data in notices:
        combined += b'\n\n===== ' + name.encode() + b' =====\n\n' + data
    return {'FrameSetup.exe': exe,
            'LICENSE.txt': intro + kit['LICENSE'],
            'THIRD_PARTY_LICENSES.txt': combined}
