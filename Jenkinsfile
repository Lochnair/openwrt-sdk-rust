def getTargets(String targetParam) {
    if (targetParam == 'all') {
        sh(
            script: '''
                git clone --depth 1 https://github.com/openwrt/openwrt.git /tmp/openwrt-targets
                cd /tmp/openwrt-targets
                perl scripts/dump-target-info.pl targets 2>/dev/null | awk '{print $1}'
            ''',
            returnStdout: true
        ).trim().split('\n').toList()
    } else {
        targetParam.split(',').collect { it.trim() }.findAll { it }
    }
}

def generateStages(openwrtVersions, targets) {
    def stages = [:]

    for (version in openwrtVersions) {
        for (target in targets) {
            def v = version
            def t = target
            def safeTarget = t.replace('/', '-')
            def tag = "lochnair/openwrt-sdk-rust:${safeTarget}-${v}"

            stages["${safeTarget}-${v}"] = {
                stage("${safeTarget}-${v}") {
                    docker.withRegistry('https://index.docker.io/v1/', '868470c9-42ed-409b-af45-d23528abb4af') {
                        def img = docker.build(tag,
                            "--build-arg SAFE_TARGET=${safeTarget} " +
                            "--build-arg VERSION=${v} " +
                            ".")
                        img.push()
                    }
                }
            }
        }
    }
    return stages
}

properties([
    parameters([
        string(
            name: 'TARGETS',
            defaultValue: 'x86/64,mediatek/filogic,octeon/generic',
            description: 'Comma-separated list of targets to build, or "all" to build every target'
        )
    ])
])

node {
    stage('Checkout') {
        checkout scm
    }

    stage('Fetch Versions') {
        def versionsJson = sh(
            script: 'curl -sL https://downloads.openwrt.org/.versions.json',
            returnStdout: true
        ).trim()
        def versions = readJSON text: versionsJson

        env.STABLE = versions.stable_version ?: ''
        env.OLDSTABLE = versions.oldstable_version ?: ''
    }

    stage('Resolve Targets') {
        env.RESOLVED_TARGETS = getTargets(params.TARGETS).join(',')
        echo "Building for targets: ${env.RESOLVED_TARGETS}"
    }

    stage('Build Images') {
        def openwrtVersions = [env.STABLE, env.OLDSTABLE].findAll { it }
        def targets = env.RESOLVED_TARGETS.split(',').toList()

        def stages = generateStages(openwrtVersions, targets)
        stages.each { name, s -> s() }
    }

    stage('Cleanup') {
        sh 'rm -rf /tmp/openwrt-targets'
    }
}
